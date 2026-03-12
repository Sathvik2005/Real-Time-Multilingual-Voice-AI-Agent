"""
Text-to-Speech engine — ElevenLabs streaming synthesis.

ElevenLabs returns audio in small chunks (MP3 / PCM fragments) which we
forward directly to the WebSocket, achieving sub-100 ms time-to-first-audio.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Optional

import structlog

from backend.config import settings

logger = structlog.get_logger(__name__)

# Per-language voice override map — populated from env vars at import time.
# Set ELEVENLABS_VOICE_ID_HI, ELEVENLABS_VOICE_ID_TA, etc. to override per language.
def _build_voice_map() -> dict[str, str]:
    import os
    lang_codes = ["hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa", "or"]
    result: dict[str, str] = {}
    for code in lang_codes:
        env_key = f"ELEVENLABS_VOICE_ID_{code.upper()}"
        val = os.environ.get(env_key) or getattr(settings, f"ELEVENLABS_VOICE_ID_{code.upper()}", None)
        if val:
            result[code] = val
    return result

_LANGUAGE_VOICE_MAP: dict[str, str] = _build_voice_map()


def _voice_id_for_language(language: str) -> str:
    return _LANGUAGE_VOICE_MAP.get(language, settings.ELEVENLABS_VOICE_ID)


def _get_client():
    """Lazy-load ElevenLabs client to avoid pydantic model-construction at import time."""
    # Import here to defer heavy pydantic-schema work until first use
    from elevenlabs.client import ElevenLabs  # noqa: PLC0415
    return ElevenLabs(api_key=settings.ELEVENLABS_API_KEY)


class ElevenLabsTTS:

    def __init__(self) -> None:
        self._client = None  # lazy-initialised on first use

    def _ensure_client(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    # ── Non-streaming (buffer full audio) ────────────────────────────────

    async def synthesize(
        self,
        text: str,
        language: str = "en",
    ) -> bytes:
        """Synthesise text and return the complete audio as bytes."""
        if not text.strip():
            return b""

        t0 = time.perf_counter()
        voice_id = _voice_id_for_language(language)
        client = self._ensure_client()

        try:
            loop = asyncio.get_running_loop()
            audio_bytes: bytes = await loop.run_in_executor(
                None,
                lambda: b"".join(
                    client.generate(
                        text=text,
                        voice=voice_id,
                        model=settings.ELEVENLABS_MODEL_ID,
                        output_format="mp3_44100_128",
                        stream=True,
                    )
                ),
            )
        except Exception as exc:
            logger.error("ElevenLabs synthesis failed", error=str(exc))
            return b""

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("TTS_latency", ms=round(elapsed_ms, 1), chars=len(text), bytes=len(audio_bytes))
        return audio_bytes

    # ── Streaming (yield chunks for WebSocket forwarding) ─────────────────

    async def synthesize_stream(
        self,
        text: str,
        language: str = "en",
    ) -> AsyncIterator[bytes]:
        """Stream audio chunks from ElevenLabs."""
        if not text.strip():
            return

        t0 = time.perf_counter()
        first_chunk_logged = False
        voice_id = _voice_id_for_language(language)
        client = self._ensure_client()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        def _stream_in_thread() -> None:
            try:
                generator = client.generate(
                    text=text,
                    voice=voice_id,
                    model=settings.ELEVENLABS_MODEL_ID,
                    output_format="mp3_44100_128",
                    stream=True,
                )
                for chunk in generator:
                    if chunk:
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:
                logger.error("ElevenLabs streaming failed", error=str(exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        loop.run_in_executor(None, _stream_in_thread)

        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            if not first_chunk_logged:
                first_chunk_ms = (time.perf_counter() - t0) * 1000
                logger.info("TTS_first_chunk_latency", ms=round(first_chunk_ms, 1))
                first_chunk_logged = True
            yield chunk

        total_ms = (time.perf_counter() - t0) * 1000
        logger.info("TTS_latency", ms=round(total_ms, 1), chars=len(text))


# Module-level singleton
elevenlabs_tts = ElevenLabsTTS()
