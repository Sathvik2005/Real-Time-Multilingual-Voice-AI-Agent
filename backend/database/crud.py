"""
CRUD operations for all entities.

All functions are async and accept an AsyncSession.
Business-critical writes (booking/cancellation) use SELECT FOR UPDATE
where supported, otherwise rely on the unique slot constraint as the
last line of defence against double-booking.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database.models import (
    Appointment,
    AppointmentStatus,
    Doctor,
    Patient,
    PatientPreference,
    Slot,
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Doctors
# ─────────────────────────────────────────────────────────────────────────────


async def get_doctor_by_id(db: AsyncSession, doctor_id: int) -> Optional[Doctor]:
    result = await db.execute(
        select(Doctor).where(Doctor.doctor_id == doctor_id, Doctor.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def search_doctors(
    db: AsyncSession,
    specialization: Optional[str] = None,
    name_fragment: Optional[str] = None,
    language: Optional[str] = None,
) -> Sequence[Doctor]:
    stmt = select(Doctor).where(Doctor.is_active.is_(True))

    if specialization:
        stmt = stmt.where(
            Doctor.specialization.ilike(f"%{specialization}%")
        )
    if name_fragment:
        stmt = stmt.where(Doctor.name.ilike(f"%{name_fragment}%"))

    result = await db.execute(stmt)
    doctors = result.scalars().all()

    # Filter by language support (stored as JSON text)
    if language and language != "en":
        doctors = [
            d for d in doctors if language in json.loads(d.languages_supported or "[]")
        ]

    return doctors


async def list_all_doctors(db: AsyncSession) -> Sequence[Doctor]:
    result = await db.execute(
        select(Doctor).where(Doctor.is_active.is_(True)).order_by(Doctor.name)
    )
    return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# Slots
# ─────────────────────────────────────────────────────────────────────────────


async def get_available_slots(
    db: AsyncSession,
    doctor_id: int,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 10,
) -> Sequence[Slot]:
    now = from_dt or datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = (
        select(Slot)
        .where(
            and_(
                Slot.doctor_id == doctor_id,
                Slot.is_available.is_(True),
                Slot.start_time >= now,
            )
        )
        .order_by(Slot.start_time)
        .limit(limit)
    )
    if to_dt:
        stmt = stmt.where(Slot.start_time <= to_dt)

    result = await db.execute(stmt)
    return result.scalars().all()


async def get_slot_by_id(db: AsyncSession, slot_id: int) -> Optional[Slot]:
    result = await db.execute(select(Slot).where(Slot.slot_id == slot_id))
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Patients
# ─────────────────────────────────────────────────────────────────────────────


async def get_or_create_patient(
    db: AsyncSession,
    name: str,
    phone: Optional[str] = None,
    preferred_language: str = "en",
) -> Patient:
    """
    Return an existing patient matched by phone, or create a new record.
    """
    if phone:
        result = await db.execute(
            select(Patient).where(Patient.phone == phone)
        )
        patient = result.scalar_one_or_none()
        if patient:
            return patient

    patient = Patient(
        name=name,
        phone=phone,
        preferred_language=preferred_language,
    )
    db.add(patient)
    await db.flush()  # populate patient_id without committing
    logger.info("Patient created", patient_id=patient.patient_id, name=name)
    return patient


async def get_patient_by_id(db: AsyncSession, patient_id: int) -> Optional[Patient]:
    result = await db.execute(
        select(Patient)
        .options(selectinload(Patient.preferences))
        .where(Patient.patient_id == patient_id)
    )
    return result.scalar_one_or_none()


async def update_patient_language(
    db: AsyncSession, patient_id: int, language: str
) -> None:
    await db.execute(
        update(Patient)
        .where(Patient.patient_id == patient_id)
        .values(preferred_language=language)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Appointments
# ─────────────────────────────────────────────────────────────────────────────


async def create_appointment(
    db: AsyncSession,
    patient_id: int,
    doctor_id: int,
    slot_id: int,
    reason: Optional[str] = None,
) -> Appointment:
    """
    Book an appointment.  The unique constraint on slot_id prevents
    double-booking at the database level even under concurrent requests.
    """
    # Verify slot is still available (optimistic check)
    slot = await get_slot_by_id(db, slot_id)
    if slot is None or not slot.is_available:
        raise ValueError(f"Slot {slot_id} is not available")

    if slot.start_time < datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError("Cannot book a slot in the past")

    # Mark slot as taken
    await db.execute(
        update(Slot).where(Slot.slot_id == slot_id).values(is_available=False)
    )

    appointment = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        slot_id=slot_id,
        reason=reason,
        status=AppointmentStatus.SCHEDULED,
    )
    db.add(appointment)
    await db.flush()

    logger.info(
        "Appointment created",
        appointment_id=appointment.appointment_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        slot_id=slot_id,
    )
    return appointment


async def cancel_appointment(
    db: AsyncSession, appointment_id: int, patient_id: int
) -> Appointment:
    result = await db.execute(
        select(Appointment)
        .options(selectinload(Appointment.slot))
        .where(
            and_(
                Appointment.appointment_id == appointment_id,
                Appointment.patient_id == patient_id,
            )
        )
    )
    appt = result.scalar_one_or_none()
    if appt is None:
        raise ValueError(f"Appointment {appointment_id} not found for patient {patient_id}")

    if appt.status in (AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED):
        raise ValueError(f"Appointment is already {appt.status.value}")

    appt.status = AppointmentStatus.CANCELLED

    # Release the slot back to the pool
    await db.execute(
        update(Slot).where(Slot.slot_id == appt.slot_id).values(is_available=True)
    )

    logger.info("Appointment cancelled", appointment_id=appointment_id)
    return appt


async def reschedule_appointment(
    db: AsyncSession,
    appointment_id: int,
    patient_id: int,
    new_slot_id: int,
) -> Appointment:
    result = await db.execute(
        select(Appointment)
        .options(selectinload(Appointment.slot))
        .where(
            and_(
                Appointment.appointment_id == appointment_id,
                Appointment.patient_id == patient_id,
            )
        )
    )
    appt = result.scalar_one_or_none()
    if appt is None:
        raise ValueError(f"Appointment {appointment_id} not found")

    if appt.status in (AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED):
        raise ValueError(f"Cannot reschedule a {appt.status.value} appointment")

    new_slot = await get_slot_by_id(db, new_slot_id)
    if new_slot is None or not new_slot.is_available:
        raise ValueError(f"New slot {new_slot_id} is not available")

    if new_slot.start_time < datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError("Cannot reschedule to a past slot")

    # Release old slot
    await db.execute(
        update(Slot).where(Slot.slot_id == appt.slot_id).values(is_available=True)
    )

    # Reserve new slot
    await db.execute(
        update(Slot).where(Slot.slot_id == new_slot_id).values(is_available=False)
    )

    appt.slot_id = new_slot_id
    appt.status = AppointmentStatus.RESCHEDULED

    logger.info(
        "Appointment rescheduled",
        appointment_id=appointment_id,
        old_slot=appt.slot_id,
        new_slot=new_slot_id,
    )
    return appt


async def get_patient_appointments(
    db: AsyncSession,
    patient_id: int,
    include_cancelled: bool = False,
) -> Sequence[Appointment]:
    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.doctor),
            selectinload(Appointment.slot),
        )
        .where(Appointment.patient_id == patient_id)
        .order_by(Appointment.created_at.desc())
    )
    if not include_cancelled:
        stmt = stmt.where(
            Appointment.status.notin_(
                [AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED]
            )
        )
    result = await db.execute(stmt)
    return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# Patient Preferences
# ─────────────────────────────────────────────────────────────────────────────


async def upsert_patient_preferences(
    db: AsyncSession,
    patient_id: int,
    preferred_language: Optional[str] = None,
    preferred_specializations: Optional[List[str]] = None,
) -> PatientPreference:
    result = await db.execute(
        select(PatientPreference).where(PatientPreference.patient_id == patient_id)
    )
    prefs = result.scalar_one_or_none()

    if prefs is None:
        prefs = PatientPreference(patient_id=patient_id)
        db.add(prefs)

    if preferred_language:
        prefs.preferred_language = preferred_language
    if preferred_specializations is not None:
        prefs.preferred_specializations = json.dumps(preferred_specializations)

    prefs.last_interaction_at = datetime.now(timezone.utc).replace(tzinfo=None)
    prefs.interaction_count = (prefs.interaction_count or 0) + 1

    await db.flush()
    return prefs
