"""
Database seed script.

Populates the database with realistic demo data:
  * 12 doctors across 8 specializations
  * 7-day rolling availability (10 slots per doctor per day)
  * 3 sample patients

Run with:
  python -m scripts.seed_database
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the repo root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.connection import get_session_factory, init_db
from backend.database.models import Doctor, Patient, PatientPreference, Slot

DOCTORS = [
    {
        "name": "Dr. Arjun Sharma",
        "specialization": "Cardiology",
        "languages": ["en", "hi"],
        "bio": "Senior Cardiologist with 18 years of experience in interventional cardiology.",
    },
    {
        "name": "Dr. Priya Nair",
        "specialization": "Neurology",
        "languages": ["en", "ml", "ta"],
        "bio": "Neurologist specialising in headache disorders and movement conditions.",
    },
    {
        "name": "Dr. Rahul Mehta",
        "specialization": "Dermatology",
        "languages": ["en", "hi", "gu"],
        "bio": "Dermatologist and cosmetologist with expertise in skin conditions.",
    },
    {
        "name": "Dr. Sanya Kapoor",
        "specialization": "Orthopaedics",
        "languages": ["en", "hi", "pa"],
        "bio": "Orthopaedic surgeon specialising in joint replacement and sports injuries.",
    },
    {
        "name": "Dr. Vikram Reddy",
        "specialization": "Gastroenterology",
        "languages": ["en", "te", "hi"],
        "bio": "Gastroenterologist with focus on liver and inflammatory bowel disease.",
    },
    {
        "name": "Dr. Ananya Desai",
        "specialization": "Pulmonology",
        "languages": ["en", "mr", "gu"],
        "bio": "Pulmonologist with expertise in asthma, COPD and sleep disorders.",
    },
    {
        "name": "Dr. Kumar Iyer",
        "specialization": "General Medicine",
        "languages": ["en", "ta", "kn"],
        "bio": "General physician offering comprehensive primary care services.",
    },
    {
        "name": "Dr. Fatima Khan",
        "specialization": "Paediatrics",
        "languages": ["en", "hi", "ur"],
        "bio": "Paediatrician caring for newborns through adolescents.",
    },
    {
        "name": "Dr. Carlos Rivera",
        "specialization": "Psychiatry",
        "languages": ["en", "es"],
        "bio": "Psychiatrist specialising in anxiety, depression and cognitive behavioural therapy.",
    },
    {
        "name": "Dr. Sophie Laurent",
        "specialization": "Gynaecology",
        "languages": ["en", "fr"],
        "bio": "Gynaecologist and obstetrician with 15 years of clinical experience.",
    },
    {
        "name": "Dr. Li Wei",
        "specialization": "Ophthalmology",
        "languages": ["en", "zh-cn"],
        "bio": "Ophthalmologist specialising in cataract surgery and diabetic eye disease.",
    },
    {
        "name": "Dr. Hannah Müller",
        "specialization": "Endocrinology",
        "languages": ["en", "de"],
        "bio": "Endocrinologist focusing on diabetes, thyroid and metabolic disorders.",
    },
]

PATIENTS = [
    {"name": "Ravi Kumar",    "phone": "+919876543210", "preferred_language": "hi"},
    {"name": "Meera Pillai",  "phone": "+919823456789", "preferred_language": "ml"},
    {"name": "John Smith",    "phone": "+19515554321",  "preferred_language": "en"},
]

SLOT_TIMES = [
    (9, 0),  (9, 30),
    (10, 0), (10, 30),
    (11, 0), (11, 30),
    (14, 0), (14, 30),
    (15, 0), (15, 30),
    (17, 0), (17, 30),
    (18, 0), (18, 30),
]


async def seed(db: AsyncSession) -> None:
    # ── Doctors ───────────────────────────────────────────────────────────
    print("Seeding doctors...")
    doctors: list[Doctor] = []
    for d in DOCTORS:
        doc = Doctor(
            name=d["name"],
            specialization=d["specialization"],
            languages_supported=json.dumps(d["languages"]),
            bio=d.get("bio", ""),
            is_active=True,
        )
        db.add(doc)
        doctors.append(doc)

    await db.flush()

    # ── Slots (7-day rolling window) ───────────────────────────────────────
    print("Generating availability slots...")
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    for day_offset in range(1, 8):          # tomorrow through +7 days
        slot_date = now + timedelta(days=day_offset)
        for doctor in doctors:
            for hour, minute in SLOT_TIMES:
                start = slot_date.replace(hour=hour, minute=minute, second=0)
                end = start + timedelta(minutes=30)
                db.add(Slot(
                    doctor_id=doctor.doctor_id,
                    start_time=start,
                    end_time=end,
                    is_available=True,
                ))

    # ── Patients ───────────────────────────────────────────────────────────
    print("Seeding patients...")
    for p in PATIENTS:
        patient = Patient(
            name=p["name"],
            phone=p["phone"],
            preferred_language=p["preferred_language"],
        )
        db.add(patient)
        await db.flush()

        pref = PatientPreference(
            patient_id=patient.patient_id,
            preferred_language=p["preferred_language"],
        )
        db.add(pref)

    await db.commit()
    print(f"Seeded {len(DOCTORS)} doctors, {len(PATIENTS)} patients, and availability for 7 days.")


async def main() -> None:
    await init_db()
    factory = get_session_factory()
    async with factory() as db:
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
