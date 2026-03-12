"""
Scheduling engine — slot management and conflict resolution.

Responsibilities
----------------
* Find available slots matching natural-language time preferences
  (e.g. "tomorrow evening", "next Monday morning").
* Suggest alternative slots when the requested one is taken.
* Prevent double-booking via the database unique constraint (primary guard)
  and an optimistic availability check (secondary guard).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud
from backend.database.models import Doctor, Slot

logger = structlog.get_logger(__name__)

# ── Time-of-day bands ─────────────────────────────────────────────────────────
TIME_BANDS: Dict[str, Tuple[int, int]] = {
    "morning":   (8,  12),
    "afternoon": (12, 17),
    "evening":   (17, 21),
    "night":     (21, 23),
}

# ── Day offset keywords ───────────────────────────────────────────────────────
_DAY_KEYWORDS: Dict[str, int] = {
    "today":         0,
    "tomorrow":      1,
    "day after":     2,
    "this week":     0,
    "next week":     7,
}


class SchedulingEngine:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    async def find_slots(
        self,
        doctor_id: int,
        time_preference: str = "",
        limit: int = 5,
    ) -> Sequence[Slot]:
        """
        Return available slots for ``doctor_id``, optionally filtered by a
        human-readable time preference such as "tomorrow evening".
        """
        from_dt, to_dt = self._parse_time_preference(time_preference)
        slots = await crud.get_available_slots(
            self._db, doctor_id, from_dt=from_dt, to_dt=to_dt, limit=limit
        )
        logger.debug(
            "Slots found",
            doctor_id=doctor_id,
            count=len(slots),
            time_preference=time_preference or "any",
        )
        return slots

    async def find_slots_by_specialization(
        self,
        specialization: str,
        time_preference: str = "",
        language: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """
        Search doctors by specialization then aggregate their available slots.
        Returns a flat list of slot dicts with embedded doctor info.
        """
        doctors = await crud.search_doctors(
            self._db, specialization=specialization, language=language
        )
        if not doctors:
            return []

        from_dt, to_dt = self._parse_time_preference(time_preference)
        results: List[Dict] = []

        for doctor in doctors:
            slots = await crud.get_available_slots(
                self._db,
                doctor.doctor_id,
                from_dt=from_dt,
                to_dt=to_dt,
                limit=limit,
            )
            for slot in slots:
                results.append(self._slot_to_dict(slot, doctor))
                if len(results) >= limit:
                    return results

        return results

    async def get_next_available(
        self,
        doctor_id: int,
        after: Optional[datetime] = None,
    ) -> Optional[Slot]:
        """Return the very next available slot for a doctor after a given time."""
        slots = await crud.get_available_slots(
            self._db, doctor_id, from_dt=after, limit=1
        )
        return slots[0] if slots else None

    async def suggest_alternatives(
        self,
        doctor_id: int,
        unavailable_slot_id: int,
        count: int = 3,
    ) -> Sequence[Slot]:
        """
        Suggest ``count`` alternative slots for a doctor when the requested
        slot is no longer available.
        """
        all_slots = await crud.get_available_slots(
            self._db, doctor_id, limit=count + 1
        )
        return [s for s in all_slots if s.slot_id != unavailable_slot_id][:count]

    # ─────────────────────────────────────────────────────────────────────
    # Formatting helpers
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _slot_to_dict(slot: Slot, doctor: Doctor) -> Dict:
        return {
            "slot_id": slot.slot_id,
            "doctor_id": doctor.doctor_id,
            "doctor_name": doctor.name,
            "specialization": doctor.specialization,
            "start_time": slot.start_time.isoformat(),
            "end_time": slot.end_time.isoformat(),
            "display_time": slot.start_time.strftime("%A, %d %B %Y at %I:%M %p"),
        }

    @staticmethod
    def format_slots_for_agent(slots: Sequence[Slot], doctor: Optional[Doctor] = None) -> str:
        """Return a human-readable numbered list of slots for the agent prompt."""
        if not slots:
            return "No available slots found."
        lines = []
        for i, slot in enumerate(slots, 1):
            label = slot.start_time.strftime("%A, %d %B at %I:%M %p")
            if doctor:
                lines.append(f"{i}. {label} with Dr. {doctor.name}")
            else:
                lines.append(f"{i}. {label}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # Time preference parser
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_time_preference(
        preference: str,
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Convert a free-text time preference into a (from_dt, to_dt) window.
        Falls back to (now, None) when the preference is unrecognised.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        preference_lower = preference.lower().strip()

        if not preference_lower:
            return now, None

        # Detect day offset
        day_offset = 0
        for keyword, offset in _DAY_KEYWORDS.items():
            if keyword in preference_lower:
                day_offset = offset
                break

        base_date = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Detect time-of-day band
        for band, (start_hour, end_hour) in TIME_BANDS.items():
            if band in preference_lower:
                from_dt = base_date.replace(hour=start_hour)
                to_dt = base_date.replace(hour=end_hour, minute=59, second=59)
                return from_dt, to_dt

        # Specific hour pattern e.g. "6 PM", "18:00"
        hour_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', preference_lower)
        if hour_match:
            hour = int(hour_match.group(1))
            meridiem = hour_match.group(3)
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            from_dt = base_date.replace(hour=hour)
            to_dt = from_dt + timedelta(hours=1)
            return from_dt, to_dt

        # Default: entire base day
        return base_date, base_date.replace(hour=23, minute=59, second=59)
