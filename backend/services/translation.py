"""
Translation service.

Primary:  deep-translator's GoogleTranslate backend (no API key required
          for small volumes; replace with DeepL or LibreTranslate for
          production throughput).

The service caches translations for identical (text, src, tgt) tuples
within the process lifetime to avoid redundant API round-trips.  For a
high-throughput deployment, replace the in-process cache with Redis.
"""

from __future__ import annotations

import asyncio
import hashlib
from functools import lru_cache
from typing import Dict, Optional

import structlog
from deep_translator import GoogleTranslator
from deep_translator.exceptions import (
    LanguageNotSupportedException,
    NotValidPayload,
    TranslationNotFound,
)
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)

# ── Language code mapping ─────────────────────────────────────────────────────
# deep-translator uses slightly different codes for some languages
_CODE_MAP: Dict[str, str] = {
    "zh-cn": "zh-CN",
    "pa":    "pa",   # Punjabi supported via Google Translate
}


def _normalise_code(code: str) -> str:
    return _CODE_MAP.get(code, code)


# Simple in-process translation cache
_CACHE: Dict[str, str] = {}
_CACHE_MAX_SIZE = 2048


def _cache_key(text: str, src: str, tgt: str) -> str:
    return hashlib.md5(f"{src}|{tgt}|{text[:200]}".encode()).hexdigest()


class TranslationService:

    @staticmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=False,
    )
    def _translate_sync(text: str, source: str, target: str) -> str:
        """Synchronous translation with retry logic."""
        src = _normalise_code(source)
        tgt = _normalise_code(target)
        translator = GoogleTranslator(source=src, target=tgt)
        result = translator.translate(text)
        return result or text

    async def translate(
        self, text: str, source: str = "auto", target: str = "en"
    ) -> str:
        """
        Asynchronously translate ``text`` from ``source`` to ``target``.
        Returns the original text unchanged if source == target.
        """
        if source == target or (source != "auto" and source[:2] == target[:2]):
            return text

        if not text or not text.strip():
            return text

        key = _cache_key(text, source, target)
        if key in _CACHE:
            return _CACHE[key]

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None, self._translate_sync, text, source, target
            )
        except (LanguageNotSupportedException, NotValidPayload, TranslationNotFound) as exc:
            logger.warning(
                "Translation failed, returning original text",
                source=source,
                target=target,
                error=str(exc),
            )
            return text
        except Exception as exc:
            logger.error("Unexpected translation error", error=str(exc))
            return text

        # Evict oldest entries if cache is full
        if len(_CACHE) >= _CACHE_MAX_SIZE:
            oldest_key = next(iter(_CACHE))
            del _CACHE[oldest_key]

        _CACHE[key] = result

        logger.debug(
            "Translated",
            source=source,
            target=target,
            input_length=len(text),
            output_length=len(result),
        )
        return result

    async def to_english(self, text: str, source: str) -> str:
        """Translate text from ``source`` language to English."""
        if source == "en":
            return text
        return await self.translate(text, source=source, target="en")

    async def from_english(self, text: str, target: str) -> str:
        """Translate English text to ``target`` language."""
        if target == "en":
            return text
        return await self.translate(text, source="en", target=target)


# Module-level singleton
translation_service = TranslationService()
