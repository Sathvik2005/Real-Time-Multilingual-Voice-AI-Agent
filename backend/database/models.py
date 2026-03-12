"""
SQLAlchemy ORM models.

Tables
------
doctors           — Physician registry
patients          — Patient registry
slots             — Doctor availability time slots
appointments      — Booked appointments
patient_preferences — Per-patient language / preference store
campaigns         — Outbound reminder campaigns
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"


class CampaignStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Doctor
# ─────────────────────────────────────────────────────────────────────────────


class Doctor(Base):
    __tablename__ = "doctors"

    doctor_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    specialization: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # JSON-encoded list of BCP-47 language tags e.g. ["en","hi","ta"]
    languages_supported: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    slots: Mapped[List[Slot]] = relationship(
        "Slot", back_populates="doctor", cascade="all, delete-orphan"
    )
    appointments: Mapped[List[Appointment]] = relationship(
        "Appointment", back_populates="doctor"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Patient
# ─────────────────────────────────────────────────────────────────────────────


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    preferred_doctor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("doctors.doctor_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    appointments: Mapped[List[Appointment]] = relationship(
        "Appointment", back_populates="patient"
    )
    preferred_doctor: Mapped[Optional[Doctor]] = relationship(
        "Doctor", foreign_keys=[preferred_doctor_id]
    )
    preferences: Mapped[Optional[PatientPreference]] = relationship(
        "PatientPreference", back_populates="patient", uselist=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Slot
# ─────────────────────────────────────────────────────────────────────────────


class Slot(Base):
    __tablename__ = "slots"

    slot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("doctors.doctor_id"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    doctor: Mapped[Doctor] = relationship("Doctor", back_populates="slots")
    appointment: Mapped[Optional[Appointment]] = relationship(
        "Appointment", back_populates="slot", uselist=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Appointment
# ─────────────────────────────────────────────────────────────────────────────


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("slot_id", name="uq_appointments_slot"),  # prevent double-book
    )

    appointment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.patient_id"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("doctors.doctor_id"), nullable=False, index=True
    )
    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("slots.slot_id"), nullable=False
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), nullable=False, default=AppointmentStatus.SCHEDULED
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped[Patient] = relationship("Patient", back_populates="appointments")
    doctor: Mapped[Doctor] = relationship("Doctor", back_populates="appointments")
    slot: Mapped[Slot] = relationship("Slot", back_populates="appointment")


# ─────────────────────────────────────────────────────────────────────────────
# PatientPreference
# ─────────────────────────────────────────────────────────────────────────────


class PatientPreference(Base):
    __tablename__ = "patient_preferences"

    preference_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.patient_id"), nullable=False, unique=True
    )
    preferred_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    # JSON-encoded list: ["Cardiology", "Dermatology"]
    preferred_specializations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON-encoded list: ["morning", "evening"]
    preferred_time_slots: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_interaction_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    interaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped[Patient] = relationship("Patient", back_populates="preferences")


# ─────────────────────────────────────────────────────────────────────────────
# Campaign
# ─────────────────────────────────────────────────────────────────────────────


class Campaign(Base):
    __tablename__ = "campaigns"

    campaign_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), nullable=False, default=CampaignStatus.PENDING
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # JSON-encoded list of appointment_ids targeted by this campaign
    appointment_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
