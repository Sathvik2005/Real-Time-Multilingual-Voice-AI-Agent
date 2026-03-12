"""
Redis-backed session memory.

Each session stores conversation state with a configurable TTL (default 30 min).
All data is JSON-serialised.  The session key format is:

    session:{session_id}

Sub-keys are stored in a Redis Hash so individual fields can be updated
without re-serialising the entire session object.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog
from redis.asyncio import Redis, from_url

from backend.config import settings

logger = structlog.get_logger(__name__)

# ── Session field names ───────────────────────────────────────────────────────
FIELD_LANGUAGE = "language"
FIELD_PATIENT_ID = "patient_id"
FIELD_PATIENT_NAME = "patient_name"
FIELD_CURRENT_INTENT = "current_intent"
FIELD_SELECTED_DOCTOR = "selected_doctor"      # JSON-encoded dict
FIELD_SELECTED_SLOT = "selected_slot"          # JSON-encoded dict
FIELD_PENDING_CONFIRM = "pending_confirmation" # JSON-encoded dict
FIELD_CONVERSATION_TURN = "conversation_turn"
FIELD_LAST_ACTIVITY = "last_activity"


# ── In-memory fallback store ──────────────────────────────────────────────────

class _InMemoryStore:
    """Simple in-process session store used when Redis is unavailable."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def ping(self) -> bool:
        return True

    async def hset(self, key: str, field: str = None, value: str = None, mapping: dict = None) -> None:
        if key not in self._data:
            self._data[key] = {}
        if mapping:
            self._data[key].update(mapping)
        elif field is not None:
            self._data[key][field] = value

    async def hget(self, key: str, field: str) -> Optional[str]:
        return self._data.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict:
        return dict(self._data.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> None:
        for f in fields:
            self._data.get(key, {}).pop(f, None)

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        val = int(self._data.get(key, {}).get(field, "0")) + amount
        if key not in self._data:
            self._data[key] = {}
        self._data[key][field] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> None:
        pass  # no-op

    async def exists(self, key: str) -> int:
        return 1 if key in self._data else 0

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def aclose(self) -> None:
        pass


class RedisSessionManager:
    """Manages lifecycle of agent sessions in Redis."""

    def __init__(self, redis_url: str = settings.REDIS_URL) -> None:
        self._url = redis_url
        self._client: Optional[Redis] = None

    # ── Connection ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._client = from_url(self._url, decode_responses=True)
        await self._client.ping()
        logger.info("Redis session store connected", url=self._url.split("@")[-1])

    def use_fallback(self) -> None:
        """Switch to in-memory store when Redis is unavailable."""
        self._client = _InMemoryStore()  # type: ignore[assignment]

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("RedisSessionManager not connected. Call connect() first.")
        return self._client  # type: ignore[return-value]

    # ── Key helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def create_session(self, session_id: str, language: str = "en") -> None:
        key = self._key(session_id)
        await self.client.hset(
            key,
            mapping={
                FIELD_LANGUAGE: language,
                FIELD_CONVERSATION_TURN: "0",
                FIELD_LAST_ACTIVITY: datetime.now(timezone.utc).isoformat(),
            },
        )
        await self.client.expire(key, settings.SESSION_TTL_SECONDS)
        logger.debug("Session created", session_id=session_id)

    async def session_exists(self, session_id: str) -> bool:
        return bool(await self.client.exists(self._key(session_id)))

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        data = await self.client.hgetall(self._key(session_id))
        # Deserialise JSON fields
        for field in (FIELD_SELECTED_DOCTOR, FIELD_SELECTED_SLOT, FIELD_PENDING_CONFIRM):
            if field in data and data[field]:
                try:
                    data[field] = json.loads(data[field])
                except (json.JSONDecodeError, TypeError):
                    data[field] = None
        return data

    async def set_field(self, session_id: str, field: str, value: Any) -> None:
        key = self._key(session_id)
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        await self.client.hset(key, field, str(value))
        await self.client.expire(key, settings.SESSION_TTL_SECONDS)

    async def get_field(self, session_id: str, field: str) -> Optional[str]:
        return await self.client.hget(self._key(session_id), field)

    async def set_language(self, session_id: str, language: str) -> None:
        await self.set_field(session_id, FIELD_LANGUAGE, language)

    async def get_language(self, session_id: str) -> str:
        lang = await self.get_field(session_id, FIELD_LANGUAGE)
        return lang or "en"

    async def set_patient(self, session_id: str, patient_id: int, name: str) -> None:
        await self.set_field(session_id, FIELD_PATIENT_ID, str(patient_id))
        await self.set_field(session_id, FIELD_PATIENT_NAME, name)

    async def get_patient_id(self, session_id: str) -> Optional[int]:
        val = await self.get_field(session_id, FIELD_PATIENT_ID)
        return int(val) if val else None

    async def set_selected_doctor(self, session_id: str, doctor: Dict[str, Any]) -> None:
        await self.set_field(session_id, FIELD_SELECTED_DOCTOR, doctor)

    async def get_selected_doctor(self, session_id: str) -> Optional[Dict[str, Any]]:
        val = await self.get_field(session_id, FIELD_SELECTED_DOCTOR)
        if val:
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    async def set_pending_confirmation(
        self, session_id: str, confirmation: Dict[str, Any]
    ) -> None:
        await self.set_field(session_id, FIELD_PENDING_CONFIRM, confirmation)

    async def get_pending_confirmation(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        val = await self.get_field(session_id, FIELD_PENDING_CONFIRM)
        if val:
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    async def clear_pending_confirmation(self, session_id: str) -> None:
        await self.client.hdel(self._key(session_id), FIELD_PENDING_CONFIRM)

    async def increment_turn(self, session_id: str) -> int:
        turn = await self.client.hincrby(self._key(session_id), FIELD_CONVERSATION_TURN, 1)
        await self.client.hset(
            self._key(session_id),
            FIELD_LAST_ACTIVITY,
            datetime.now(timezone.utc).isoformat(),
        )
        await self.client.expire(self._key(session_id), settings.SESSION_TTL_SECONDS)
        return int(turn)

    async def delete_session(self, session_id: str) -> None:
        await self.client.delete(self._key(session_id))
        logger.debug("Session deleted", session_id=session_id)
