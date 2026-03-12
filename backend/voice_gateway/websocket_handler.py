"""
WebSocket handler — voice and text interaction gateway.

Message protocol (JSON)
-----------------------

Client → Server:
  { "type": "init",          "patient_name": "Priya Nair" }
  { "type": "text_message",  "text": "I want to see a cardiologist" }
  { "type": "audio_start" }                         -- begin recording session
  { "type": "audio_end",     "audio": "<base64>" }  -- finalised audio buffer
  { "type": "interrupt" }                            -- barge-in / cancel TTS
  { "type": "ping" }

Server → Client:
  { "type": "session_ready",     "session_id": "...", "message": "..." }
  { "type": "transcript",        "text": "...", "is_final": true }
  { "type": "language_detected", "code": "hi", "name": "Hindi" }
  { "type": "agent_text",        "text": "..." }
  { "type": "audio_chunk",       "data": "<base64>" }
  { "type": "audio_end" }
  { "type": "latency_metrics",   "asr_ms": 130, "llm_ms": 120, "tts_ms": 90 }
  { "type": "error",             "message": "..." }
  { "type": "pong" }
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Dict, Optional

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from backend.memory.session import RedisSessionManager
from backend.services.language_detection import language_detector
from backend.voice_gateway.stream_manager import StreamManager
from backend.voice_pipeline.stt import deepgram_stt

logger = structlog.get_logger(__name__)


class VoiceWebSocketHandler:
    """
    Manages the lifecycle of one WebSocket connection.

    Responsibilities:
      * Parse incoming JSON control messages and binary audio.
      * Delegate audio → STT → StreamManager pipeline.
      * Forward TTS audio and text events back to the client.
      * Handle disconnections and errors gracefully.
    """

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        session_manager: RedisSessionManager,
    ) -> None:
        self._ws = websocket
        self.session_id = session_id
        self._session_manager = session_manager
        self._stream_manager: Optional[StreamManager] = None
        self._live_asr = None  # Deepgram live connection (optional)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def handle(self) -> None:
        await self._ws.accept()
        logger.info("WebSocket connected", session_id=self.session_id)

        self._stream_manager = StreamManager(
            session_id=self.session_id,
            session_store=self._session_manager,
            on_audio_chunk=self._send_audio_chunk,
            on_text_event=self._send_agent_text,
            on_audio_end=self._send_audio_end,
            on_latency_event=self._send_latency_metrics,
            on_tool_event=self._send_tool_event,
        )

        try:
            await self._message_loop()
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected", session_id=self.session_id)
        except Exception as exc:
            logger.error(
                "WebSocket handler error",
                session_id=self.session_id,
                error=str(exc),
            )
            await self._send_error(str(exc))
        finally:
            await self._cleanup()

    async def _message_loop(self) -> None:
        while True:
            try:
                message = await self._ws.receive()
            except WebSocketDisconnect:
                return

            # Starlette sends {"type": "websocket.disconnect"} before raising
            if message.get("type") == "websocket.disconnect":
                return

            if "text" in message:
                await self._handle_text_message(message["text"])
            elif "bytes" in message:
                await self._handle_binary_message(message["bytes"])

    # ── Message handlers ──────────────────────────────────────────────────

    async def _handle_text_message(self, raw: str) -> None:
        try:
            data: Dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await self._send_error("Invalid JSON message")
            return

        msg_type = data.get("type", "")

        if msg_type == "init":
            await self._handle_init(data)

        elif msg_type == "text_message":
            text = data.get("text", "").strip()
            if text and self._stream_manager:
                await self._stream_manager.process_text(text)

        elif msg_type == "audio_end":
            # Client sends a finalised audio buffer in base64
            audio_b64 = data.get("audio", "")
            if audio_b64:
                await self._handle_audio_buffer(audio_b64)

        elif msg_type == "interrupt":
            if self._stream_manager:
                self._stream_manager.interrupt_tts()

        elif msg_type == "audio_start":
            await self._handle_audio_start()

        elif msg_type == "ping":
            await self._ws.send_json({"type": "pong"})

    async def _handle_binary_message(self, data: bytes) -> None:
        """Forward raw audio bytes to the live Deepgram session if open."""
        if self._live_asr and data:
            await deepgram_stt.send_audio(self._live_asr, data)

    # ── Init ─────────────────────────────────────────────────────────────

    async def _handle_init(self, data: Dict[str, Any]) -> None:
        patient_name = data.get("patient_name") or "Guest"
        patient_id = await self._stream_manager.initialise(patient_name=patient_name)

        await self._ws.send_json(
            {
                "type": "session_ready",
                "session_id": self.session_id,
                "patient_id": patient_id,
                "message": f"Hello {patient_name}, how can I help you today?",
            }
        )
        logger.info("Session initialised", session_id=self.session_id, patient=patient_name)

    # ── Audio – batch processing ──────────────────────────────────────────

    async def _handle_audio_buffer(self, audio_b64: str) -> None:
        """
        Called when the client sends a complete audio segment (end-of-speech).
        Decoded → Deepgram REST transcription → StreamManager.
        """
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            await self._send_error("Invalid base64 audio data")
            return

        t0 = time.perf_counter()
        language = await self._session_manager.get_language(self.session_id)

        # ── ASR ────────────────────────────────────────────────────────
        transcript = await deepgram_stt.transcribe_bytes(audio_bytes, language=language)
        asr_ms = (time.perf_counter() - t0) * 1000

        if not transcript.strip():
            logger.debug("Empty transcript received", session_id=self.session_id)
            return

        # Detect / update language from transcript
        detected_lang = language_detector.detect(transcript)
        if detected_lang != language:
            await self._session_manager.set_language(self.session_id, detected_lang)
            await self._ws.send_json(
                {
                    "type": "language_detected",
                    "code": detected_lang,
                    "name": language_detector.display_name(detected_lang),
                }
            )

        # Send interim transcript to UI
        await self._ws.send_json(
            {"type": "transcript", "text": transcript, "is_final": True}
        )

        logger.info("ASR_latency", ms=round(asr_ms, 1), session_id=self.session_id)

        # ── Pipeline ───────────────────────────────────────────────────
        await self._stream_manager.process_text(transcript, asr_ms=asr_ms)

    # ── Audio – live streaming start ──────────────────────────────────────

    async def _handle_audio_start(self) -> None:
        """Open a Deepgram live session for continuous streaming."""
        if self._live_asr:
            return  # Already open

        language = await self._session_manager.get_language(self.session_id)

        def on_transcript(text: str, is_final: bool) -> None:
            asyncio.create_task(
                self._ws.send_json(
                    {"type": "transcript", "text": text, "is_final": is_final}
                )
            )
            if is_final and self._stream_manager:
                asyncio.create_task(self._stream_manager.process_text(text))

        self._live_asr = await deepgram_stt.create_live_session(
            on_transcript=on_transcript, language=language
        )
        logger.debug("Live ASR session opened", session_id=self.session_id)

    # ── Callbacks (called from StreamManager) ─────────────────────────────

    async def _send_audio_chunk(self, chunk: bytes) -> None:
        """Forward TTS audio chunk to the WebSocket client."""
        try:
            await self._ws.send_json(
                {"type": "audio_chunk", "data": base64.b64encode(chunk).decode()}
            )
        except Exception:
            pass  # Connection may have closed

    async def _send_agent_text(self, text: str, is_final: bool) -> None:
        try:
            await self._ws.send_json(
                {"type": "agent_text", "text": text, "is_final": is_final}
            )
        except Exception:
            pass

    async def _send_audio_end(self) -> None:
        """Called by StreamManager after all TTS chunks have been sent."""
        try:
            await self._ws.send_json({"type": "audio_end"})
        except Exception:
            pass

    async def _send_latency_metrics(self, asr_ms: float, llm_ms: float, tts_ms: float) -> None:
        """Send latency breakdown to client after each pipeline run."""
        try:
            await self._ws.send_json(
                {
                    "type": "latency_metrics",
                    "asr_ms": round(asr_ms, 1),
                    "llm_ms": round(llm_ms, 1),
                    "tts_ms": round(tts_ms, 1),
                    "total_ms": round(asr_ms + llm_ms + tts_ms, 1),
                }
            )
        except Exception:
            pass

    async def _send_tool_event(self, tool_calls: list) -> None:
        """Send reasoning trace to client so tool usage is visible."""
        try:
            await self._ws.send_json(
                {
                    "type": "tool_calls",
                    "calls": tool_calls,
                }
            )
        except Exception:
            pass

    async def _send_error(self, message: str) -> None:
        try:
            await self._ws.send_json({"type": "error", "message": message})
        except Exception:
            pass

    # ── Cleanup ───────────────────────────────────────────────────────────

    async def _cleanup(self) -> None:
        if self._live_asr:
            await deepgram_stt.close_live_session(self._live_asr)
            self._live_asr = None
