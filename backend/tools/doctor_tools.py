"""
Doctor-facing tools — symptom-based recommendation.

This module provides a lightweight symptom → specialization mapper so
the agent can suggest the right type of doctor even when the patient
does not know which specialization they need.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Optional

import structlog
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud

logger = structlog.get_logger(__name__)

# ── Symptom → specialization keyword map ─────────────────────────────────────
_SYMPTOM_MAP: Dict[str, str] = {
    # Cardiovascular
    "chest pain":         "Cardiology",
    "heart":              "Cardiology",
    "palpitation":        "Cardiology",
    "blood pressure":     "Cardiology",
    "hypertension":       "Cardiology",
    # Neurological
    "headache":           "Neurology",
    "migraine":           "Neurology",
    "seizure":            "Neurology",
    "dizziness":          "Neurology",
    "numbness":           "Neurology",
    "stroke":             "Neurology",
    # Dermatological
    "rash":               "Dermatology",
    "skin":               "Dermatology",
    "acne":               "Dermatology",
    "eczema":             "Dermatology",
    "psoriasis":          "Dermatology",
    # Orthopaedic
    "knee":               "Orthopaedics",
    "back pain":          "Orthopaedics",
    "joint":              "Orthopaedics",
    "fracture":           "Orthopaedics",
    "bone":               "Orthopaedics",
    # Gastro
    "stomach":            "Gastroenterology",
    "abdomen":            "Gastroenterology",
    "acidity":            "Gastroenterology",
    "digestion":          "Gastroenterology",
    "liver":              "Gastroenterology",
    # Respiratory
    "cough":              "Pulmonology",
    "breathing":          "Pulmonology",
    "asthma":             "Pulmonology",
    "lung":               "Pulmonology",
    # ENT
    "ear":                "ENT",
    "throat":             "ENT",
    "nose":               "ENT",
    "sinus":              "ENT",
    # Ophthalmology
    "eye":                "Ophthalmology",
    "vision":             "Ophthalmology",
    "glasses":            "Ophthalmology",
    # General
    "fever":              "General Medicine",
    "cold":               "General Medicine",
    "flu":                "General Medicine",
    "fatigue":            "General Medicine",
    "diabetes":           "Endocrinology",
    "thyroid":            "Endocrinology",
    # Psychiatry
    "anxiety":            "Psychiatry",
    "depression":         "Psychiatry",
    "stress":             "Psychiatry",
    "sleep":              "Psychiatry",
    # Gynaecology
    "pregnancy":          "Gynaecology",
    "menstrual":          "Gynaecology",
    "gynaec":             "Gynaecology",
    # Paediatrics
    "child":              "Paediatrics",
    "infant":             "Paediatrics",
    "baby":               "Paediatrics",
}


def get_doctor_tools(db: AsyncSession) -> List[Any]:
    """Return doctor-related tools bound to the given DB session."""

    @tool
    async def recommend_specialization(
        symptoms: Annotated[str, "Patient symptoms or health concerns in plain English"],
    ) -> str:
        """
        Recommend an appropriate medical specialization based on the patient's
        symptoms. Returns the specialization name and matched keywords.
        """
        symptoms_lower = symptoms.lower()
        matches: Dict[str, int] = {}

        for keyword, spec in _SYMPTOM_MAP.items():
            if keyword in symptoms_lower:
                matches[spec] = matches.get(spec, 0) + 1

        if not matches:
            return json.dumps({
                "specialization": "General Medicine",
                "reason": "No specific symptoms matched — recommending General Medicine for initial assessment.",
            })

        best_spec = max(matches, key=lambda k: matches[k])
        return json.dumps({
            "specialization": best_spec,
            "matched_keywords": [k for k, s in _SYMPTOM_MAP.items() if s == best_spec and k in symptoms_lower],
            "reason": f"Based on the symptoms, {best_spec} is recommended.",
        })

    @tool
    async def get_doctor_info(
        doctor_id: Annotated[int, "Doctor ID"],
    ) -> str:
        """
        Retrieve detailed information about a specific doctor.
        """
        doctor = await crud.get_doctor_by_id(db, doctor_id)
        if not doctor:
            return json.dumps({"error": f"Doctor with ID {doctor_id} not found."})

        return json.dumps({
            "doctor_id": doctor.doctor_id,
            "name": doctor.name,
            "specialization": doctor.specialization,
            "languages": json.loads(doctor.languages_supported or "[]"),
            "bio": doctor.bio or "",
        })

    @tool
    async def list_specializations() -> str:
        """
        List all unique medical specializations available in the clinic.
        """
        doctors = await crud.list_all_doctors(db)
        specs = sorted({d.specialization for d in doctors})
        return json.dumps({"specializations": specs})

    return [recommend_specialization, get_doctor_info, list_specializations]
