"""
Application configuration — loaded from environment / .env file.
All settings are validated by Pydantic at startup.
"""

from __future__ import annotations

from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────
    APP_NAME: str = "Voice AI Clinic Agent"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Server ───────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:5176",
    ]

    # ── Database ─────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/clinic.db"

    # ── Redis ────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    SESSION_TTL_SECONDS: int = 1800  # 30 minutes

    # ── OpenAI / LLM ─────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 1024

    # ── Groq (free-tier fallback LLM) ────────────────────────────────────
    # If GROQ_API_KEY is set, the agent uses Groq instead of OpenAI.
    # Get a free key at https://console.groq.com
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── Deepgram ─────────────────────────────────────────────────────────
    DEEPGRAM_API_KEY: str = ""
    DEEPGRAM_MODEL: str = "nova-2"

    # ── ElevenLabs ───────────────────────────────────────────────────────
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
    ELEVENLABS_MODEL_ID: str = "eleven_multilingual_v2"

    # ── Translation ──────────────────────────────────────────────────────
    LIBRETRANSLATE_URL: str = "https://libretranslate.com"
    LIBRETRANSLATE_API_KEY: str = ""

    # ── Celery ───────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── Feature Flags ────────────────────────────────────────────────────
    ENABLE_CAMPAIGNS: bool = True
    ENABLE_DOCTOR_RECOMMENDATION: bool = True
    ENABLE_BARGE_IN: bool = True

    # ── Latency Targets (ms) ─────────────────────────────────────────────
    TARGET_ASR_LATENCY_MS: int = 150
    TARGET_LLM_LATENCY_MS: int = 200
    TARGET_TTS_LATENCY_MS: int = 100

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> List[str]:
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v  # type: ignore[return-value]


settings = Settings()
