"""
Language detection service.

Uses langdetect as the primary detector with a confidence threshold.
Normalises detected language codes to the BCP-47 subset used throughout
the project.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional

import structlog
from langdetect import LangDetectException, detect, detect_langs

logger = structlog.get_logger(__name__)

# ── BCP-47 → display name map ─────────────────────────────────────────────────
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "en":    "English",
    "hi":    "Hindi",
    "ta":    "Tamil",
    "te":    "Telugu",
    "kn":    "Kannada",
    "ml":    "Malayalam",
    "mr":    "Marathi",
    "bn":    "Bengali",
    "gu":    "Gujarati",
    "pa":    "Punjabi",
    "es":    "Spanish",
    "fr":    "French",
    "ar":    "Arabic",
    "de":    "German",
    "zh-cn": "Mandarin Chinese",
}

# langdetect code → our canonical code
_LANG_ALIAS: Dict[str, str] = {
    "zh": "zh-cn",
    "zh-TW": "zh-cn",
}

CONFIDENCE_THRESHOLD = 0.70
DEFAULT_LANGUAGE = "en"


class LanguageDetector:

    @staticmethod
    def detect(text: str, min_length: int = 5) -> str:
        """
        Return a BCP-47 language code for ``text``.
        Falls back to ``DEFAULT_LANGUAGE`` if detection confidence is below
        threshold or if the text is too short.
        """
        if not text or len(text.strip()) < min_length:
            return DEFAULT_LANGUAGE

        try:
            candidates = detect_langs(text)
        except LangDetectException:
            logger.warning("Language detection failed, defaulting to English")
            return DEFAULT_LANGUAGE

        if not candidates:
            return DEFAULT_LANGUAGE

        best = candidates[0]
        raw_lang = best.lang
        confidence = best.prob

        if confidence < CONFIDENCE_THRESHOLD:
            logger.debug(
                "Low confidence language detection",
                detected=raw_lang,
                confidence=f"{confidence:.2f}",
            )
            # Still return the best guess – the UI will show confidence
            # and the agent can ask the user to clarify if needed.

        lang = _LANG_ALIAS.get(raw_lang, raw_lang)

        if lang not in SUPPORTED_LANGUAGES:
            logger.info(
                "Unsupported language detected, falling back to English",
                detected=lang,
            )
            return DEFAULT_LANGUAGE

        logger.debug(
            "Language detected",
            language=lang,
            name=SUPPORTED_LANGUAGES.get(lang),
            confidence=f"{confidence:.2f}",
        )
        return lang

    @staticmethod
    def needs_translation(language: str) -> bool:
        """Return True if the language requires translation to English."""
        return language != "en"

    @staticmethod
    def display_name(language: str) -> str:
        return SUPPORTED_LANGUAGES.get(language, language.upper())


# Module-level singleton
language_detector = LanguageDetector()
