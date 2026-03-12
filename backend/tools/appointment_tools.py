"""
LangChain tools for appointment management.

Every tool is a pure async function decorated with @tool.
Tools never access Redis directly; they receive the patient_id and session_id
as arguments and query/write the SQL database through the CRUD layer.

The agent receives these tools at graph-construction time via
``get_appointment_tools(db)``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

import structlog
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud
from backend.scheduling.engine import SchedulingEngine

logger = structlog.get_logger(__name__)


def get_appointment_tools(db: AsyncSession) -> List[Any]:
    """
    Return a list of LangChain tools bound to a specific database session.
    This factory pattern allows the agent to use fresh tools per request.
    """

    engine = SchedulingEngine(db)

    # ── Tool 1 : search_doctors ───────────────────────────────────────────

    @tool
    async def search_doctors(
        specialization: Annotated[str, "Medical specialization, e.g. 'Cardiology'"],
        name_fragment: Annotated[Optional[str], "Part of the doctor's name"] = None,
    ) -> str:
        """
        Search for available doctors by specialization or partial name.
        Returns a JSON list of doctor records.
        """
        doctors = await crud.search_doctors(
            db, specialization=specialization, name_fragment=name_fragment
        )
        if not doctors:
            return json.dumps({"doctors": [], "message": f"No doctors found for specialization: {specialization}"})

        return json.dumps({
            "doctors": [
                {
                    "doctor_id": d.doctor_id,
                    "name": d.name,
                    "specialization": d.specialization,
                    "languages": json.loads(d.languages_supported or "[]"),
                }
                for d in doctors
            ]
        })

    # ── Tool 2 : check_availability ──────────────────────────────────────

    @tool
    async def check_availability(
        doctor_id: Annotated[int, "Doctor ID returned by search_doctors"],
        time_preference: Annotated[
            str,
            "Natural language time preference, e.g. 'tomorrow evening', 'next Monday morning'",
        ] = "",
    ) -> str:
        """
        Check available appointment slots for a specific doctor.
        Returns a JSON list of available slots ordered by start_time.
        """
        slots = await engine.find_slots(
            doctor_id=doctor_id,
            time_preference=time_preference,
            limit=5,
        )

        doctor = await crud.get_doctor_by_id(db, doctor_id)
        if not slots:
            # Suggest next available slot regardless of time preference
            next_slot = await engine.get_next_available(doctor_id)
            if next_slot:
                return json.dumps({
                    "slots": [],
                    "message": f"No slots match your preference. Next available: {next_slot.start_time.strftime('%A %d %B at %I:%M %p')}",
                    "next_available": {
                        "slot_id": next_slot.slot_id,
                        "start_time": next_slot.start_time.isoformat(),
                        "display": next_slot.start_time.strftime("%A, %d %B at %I:%M %p"),
                    },
                })
            return json.dumps({"slots": [], "message": "No available slots found for this doctor."})

        return json.dumps({
            "doctor_name": doctor.name if doctor else "Unknown",
            "slots": [
                {
                    "slot_id": s.slot_id,
                    "start_time": s.start_time.isoformat(),
                    "end_time": s.end_time.isoformat(),
                    "display": s.start_time.strftime("%A, %d %B at %I:%M %p"),
                }
                for s in slots
            ],
        })

    # ── Tool 3 : book_appointment ────────────────────────────────────────

    @tool
    async def book_appointment(
        patient_id: Annotated[int, "Patient ID"],
        doctor_id: Annotated[int, "Doctor ID"],
        slot_id: Annotated[int, "Slot ID to book"],
        reason: Annotated[Optional[str], "Reason for the visit"] = None,
    ) -> str:
        """
        Book an appointment for a patient with a doctor at the specified slot.
        Returns confirmation details or an error message with alternatives.
        """
        try:
            appt = await crud.create_appointment(
                db, patient_id=patient_id, doctor_id=doctor_id,
                slot_id=slot_id, reason=reason
            )
            doctor = await crud.get_doctor_by_id(db, doctor_id)
            slot = await crud.get_slot_by_id(db, slot_id)

            return json.dumps({
                "success": True,
                "appointment_id": appt.appointment_id,
                "doctor_name": doctor.name if doctor else "Unknown",
                "specialization": doctor.specialization if doctor else "",
                "appointment_time": slot.start_time.strftime("%A, %d %B %Y at %I:%M %p") if slot else "",
                "status": appt.status.value,
                "message": f"Appointment confirmed with Dr. {doctor.name if doctor else 'Unknown'}.",
            })

        except ValueError as exc:
            # Slot taken — suggest alternatives
            alternatives = await engine.suggest_alternatives(doctor_id, slot_id, count=3)
            alt_list = [
                {
                    "slot_id": s.slot_id,
                    "display": s.start_time.strftime("%A, %d %B at %I:%M %p"),
                }
                for s in alternatives
            ]
            return json.dumps({
                "success": False,
                "error": str(exc),
                "alternative_slots": alt_list,
            })

    # ── Tool 4 : cancel_appointment ──────────────────────────────────────

    @tool
    async def cancel_appointment(
        patient_id: Annotated[int, "Patient ID"],
        appointment_id: Annotated[int, "Appointment ID to cancel"],
    ) -> str:
        """
        Cancel an existing appointment and release the slot.
        """
        try:
            appt = await crud.cancel_appointment(db, appointment_id, patient_id)
            return json.dumps({
                "success": True,
                "appointment_id": appt.appointment_id,
                "status": appt.status.value,
                "message": "Your appointment has been successfully cancelled.",
            })
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ── Tool 5 : reschedule_appointment ──────────────────────────────────

    @tool
    async def reschedule_appointment(
        patient_id: Annotated[int, "Patient ID"],
        appointment_id: Annotated[int, "Appointment ID to reschedule"],
        new_slot_id: Annotated[int, "New slot ID"],
    ) -> str:
        """
        Move an existing appointment to a new slot.
        Returns confirmation or an error with alternatives.
        """
        try:
            appt = await crud.reschedule_appointment(
                db, appointment_id, patient_id, new_slot_id
            )
            slot = await crud.get_slot_by_id(db, new_slot_id)
            doctor = await crud.get_doctor_by_id(db, appt.doctor_id)

            return json.dumps({
                "success": True,
                "appointment_id": appt.appointment_id,
                "new_time": slot.start_time.strftime("%A, %d %B %Y at %I:%M %p") if slot else "",
                "doctor_name": doctor.name if doctor else "Unknown",
                "status": appt.status.value,
                "message": "Your appointment has been successfully rescheduled.",
            })
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

    # ── Tool 6 : get_patient_appointments ────────────────────────────────

    @tool
    async def get_patient_appointments(
        patient_id: Annotated[int, "Patient ID"],
    ) -> str:
        """
        Retrieve the patient's upcoming appointments.
        """
        appts = await crud.get_patient_appointments(db, patient_id)
        if not appts:
            return json.dumps({"appointments": [], "message": "No upcoming appointments found."})

        return json.dumps({
            "appointments": [
                {
                    "appointment_id": a.appointment_id,
                    "doctor_name": a.doctor.name if a.doctor else "Unknown",
                    "specialization": a.doctor.specialization if a.doctor else "",
                    "time": a.slot.start_time.strftime("%A, %d %B %Y at %I:%M %p") if a.slot else "",
                    "status": a.status.value,
                }
                for a in appts
            ]
        })

    return [
        search_doctors,
        check_availability,
        book_appointment,
        cancel_appointment,
        reschedule_appointment,
        get_patient_appointments,
    ]
