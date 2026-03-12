"""
Speech-to-Text engine — Deepgram Nova streaming transcription.

The DeepgramSTT class provides:
  * Single-utterance transcription (REST)  for testing
  * Streaming transcription via WebSocket   for real-time voice pipeline

Latency is measured and emitted as a structured log entry.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Callable, Optional

import structlog
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    FileSource,
    LiveOptions,
    LiveTranscriptionEvents,
    PrerecordedOptions,
)

from backend.config import settings

logger = structlog.get_logger(__name__)

# Deepgram language code mapping from BCP-47
_DEEPGRAM_LANG_MAP = {
    "en":    "en-US",
    "hi":    "hi",
    "ta":    "ta",
    "te":    "te",
    "kn":    "kn",
    "ml":    "ml",
    "mr":    "mr",
    "bn":    "bn",
    "gu":    "gu",
    "pa":    "pa",
    "es":    "es",
    "fr":    "fr",
    "ar":    "ar",
    "de":    "de",
    "zh-cn": "zh-CN",
}


def _dg_lang(bcp47_code: str) -> str:
    return _DEEPGRAM_LANG_MAP.get(bcp47_code, "en-US")


class DeepgramSTT:
    """Wrapper around the Deepgram SDK for both batch and streaming ASR."""

    def __init__(self) -> None:
        self._client = DeepgramClient(
            settings.DEEPGRAM_API_KEY,
            DeepgramClientOptions(verbose=False),
        )

    # ── Batch transcription (for short clips / testing) ───────────────────

    async def transcribe_bytes(
        self,
        audio_bytes: bytes,
        language: str = "en",
        *,
        mimetype: str = "audio/webm",
    ) -> str:
        """
        Transcribe a complete audio buffer.  Returns the transcript text.
        Latency is logged.
        """
        t0 = time.perf_counter()

        options = PrerecordedOptions(
            model=settings.DEEPGRAM_MODEL,
            language=_dg_lang(language),
            smart_format=True,
            punctuate=True,
            diarize=False,
        )

        source: FileSource = {"buffer": audio_bytes, "mimetype": mimetype}

        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._client.listen.prerecorded.v("1").transcribe_file(
                    source, options
                ),
            )
            transcript = (
                response.results.channels[0].alternatives[0].transcript
                if response.results
                and response.results.channels
                and response.results.channels[0].alternatives
                else ""
            )
        except Exception as exc:
            logger.error("Deepgram batch transcription failed", error=str(exc))
            return ""

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("ASR_latency", ms=round(elapsed_ms, 1), chars=len(transcript))
        return transcript

    # ── Streaming transcription ────────────────────────────────────────────

    async def create_live_session(
        self,
        on_transcript: Callable[[str, bool], None],
        language: str = "en",
    ):
        """
        Open a Deepgram live connection.

        ``on_transcript(text, is_final)`` is called whenever a transcription
        result is available.  Returns the live connection object — the caller
        is responsible for sending audio chunks and closing it.
        """
        options = LiveOptions(
            model=settings.DEEPGRAM_MODEL,
            language=_dg_lang(language),
            smart_format=True,
            punctuate=True,
            interim_results=True,
            utterance_end_ms=1000,
            vad_events=True,
            endpointing=500,
        )

        connection = self._client.listen.live.v("1")

        def _on_message(self_inner, result, **kwargs):  # type: ignore[override]
            try:
                alt = result.channel.alternatives[0]
                transcript = alt.transcript
                is_final = result.is_final
                if transcript.strip():
                    on_transcript(transcript, is_final)
            except Exception:
                pass

        def _on_error(self_inner, error, **kwargs):  # type: ignore[override]
            logger.error("Deepgram live error", error=str(error))

        connection.on(LiveTranscriptionEvents.Transcript, _on_message)
        connection.on(LiveTranscriptionEvents.Error, _on_error)

        started = await asyncio.get_running_loop().run_in_executor(
            None, lambda: connection.start(options)
        )

        if not started:
            raise RuntimeError("Failed to start Deepgram live connection")

        logger.debug("Deepgram live session started", language=language)
        return connection

    async def send_audio(self, connection, audio_chunk: bytes) -> None:
        """Send a raw audio chunk to an open Deepgram live connection."""
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: connection.send(audio_chunk)
        )

    async def close_live_session(self, connection) -> None:
        await asyncio.get_running_loop().run_in_executor(
            None, connection.finish
        )
        logger.debug("Deepgram live session closed")


# Module-level singleton
deepgram_stt = DeepgramSTT()
