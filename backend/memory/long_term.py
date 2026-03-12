"""
Long-term patient memory.

Persists patient preferences and interaction history in the SQL database.
The agent queries this at the start of every session to personalise responses.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud
from backend.database.models import Patient, PatientPreference

logger = structlog.get_logger(__name__)


class LongTermMemory:
    """
    Wraps preference retrieval and update behind a clean interface
    so the agent layer does not need to know about raw ORM objects.
    """

    # ── Retrieval ─────────────────────────────────────────────────────────

    @staticmethod
    async def get_patient_context(
        db: AsyncSession, patient_id: int
    ) -> Dict[str, Any]:
        """
        Return a dict that summarises everything we know about the patient and
        that the agent can inject into the system prompt or use for personalisation.
        """
        patient = await crud.get_patient_by_id(db, patient_id)
        if patient is None:
            return {}

        context: Dict[str, Any] = {
            "patient_id": patient.patient_id,
            "name": patient.name,
            "preferred_language": patient.preferred_language,
        }

        if patient.preferred_doctor_id:
            doctor = await crud.get_doctor_by_id(db, patient.preferred_doctor_id)
            if doctor:
                context["preferred_doctor"] = {
                    "doctor_id": doctor.doctor_id,
                    "name": doctor.name,
                    "specialization": doctor.specialization,
                }

        if patient.preferences:
            prefs = patient.preferences
            context["interaction_count"] = prefs.interaction_count
            if prefs.preferred_specializations:
                try:
                    context["preferred_specializations"] = json.loads(
                        prefs.preferred_specializations
                    )
                except (json.JSONDecodeError, TypeError):
                    context["preferred_specializations"] = []

        # Recent appointment history (up to 3)
        recent_appts = await crud.get_patient_appointments(
            db, patient_id, include_cancelled=False
        )
        context["recent_appointments"] = [
            {
                "appointment_id": a.appointment_id,
                "doctor_name": a.doctor.name if a.doctor else "Unknown",
                "specialization": a.doctor.specialization if a.doctor else "",
                "start_time": a.slot.start_time.isoformat() if a.slot else "",
                "status": a.status.value,
            }
            for a in list(recent_appts)[:3]
        ]

        return context

    # ── Updates ───────────────────────────────────────────────────────────

    @staticmethod
    async def update_language(
        db: AsyncSession, patient_id: int, language: str
    ) -> None:
        await crud.update_patient_language(db, patient_id, language)
        await crud.upsert_patient_preferences(
            db, patient_id, preferred_language=language
        )
        logger.debug("Patient language updated", patient_id=patient_id, language=language)

    @staticmethod
    async def record_interaction(
        db: AsyncSession, patient_id: int
    ) -> None:
        await crud.upsert_patient_preferences(db, patient_id)
        logger.debug("Interaction recorded", patient_id=patient_id)

    @staticmethod
    async def update_preferred_doctor(
        db: AsyncSession, patient_id: int, doctor_id: int
    ) -> None:
        from sqlalchemy import update as sa_update
        from backend.database.models import Patient as PatientModel

        async with db.begin_nested():
            await db.execute(
                sa_update(PatientModel)
                .where(PatientModel.patient_id == patient_id)
                .values(preferred_doctor_id=doctor_id)
            )
        logger.debug(
            "Preferred doctor updated", patient_id=patient_id, doctor_id=doctor_id
        )
