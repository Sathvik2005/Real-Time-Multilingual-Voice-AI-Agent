"""
FastAPI application entry point.

Endpoints
---------
GET  /api/health                  — Health check
GET  /api/doctors                 — List all doctors
GET  /api/doctors/{id}/slots      — Get available slots for a doctor
POST /api/appointments            — Book an appointment (REST fallback)
GET  /api/appointments/{pid}      — Get patient appointments
WS   /ws/voice/{session_id}       — Real-time voice interaction
WS   /ws/text/{session_id}        — Text-only WebSocket (voice fallback)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import crud
from backend.database.connection import close_db, get_db, init_db
from backend.memory.session import RedisSessionManager
from backend.utils.logging_config import configure_logging
from backend.voice_gateway.websocket_handler import VoiceWebSocketHandler

configure_logging()
logger = structlog.get_logger(__name__)

# ── App state (populated at startup) ─────────────────────────────────────────

_session_manager: Optional[RedisSessionManager] = None


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session_manager

    logger.info("Starting Voice AI Clinic Agent", version=settings.APP_VERSION)
    await init_db()

    _session_manager = RedisSessionManager(settings.REDIS_URL)
    try:
        await _session_manager.connect()
    except Exception as exc:
        logger.warning(
            "Redis unavailable — running with in-memory session store",
            error=str(exc),
        )
        _session_manager.use_fallback()
    app.state.session_manager = _session_manager

    logger.info("Application startup complete")
    yield

    logger.info("Shutting down application")
    await _session_manager.disconnect()
    await close_db()
    logger.info("Shutdown complete")


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Real-time Multilingual Voice AI Clinical Appointment Agent",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST Endpoints ────────────────────────────────────────────────────────────


@app.get("/api/health", tags=["System"])
async def health_check():
    return {"status": "healthy", "version": settings.APP_VERSION}


@app.get("/api/session/new", tags=["Session"])
async def new_session():
    """Generate a new session ID for the frontend to use."""
    session_id = str(uuid.uuid4())
    return {"session_id": session_id}


@app.get("/api/doctors", tags=["Doctors"])
async def list_doctors(
    specialization: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    if specialization:
        doctors = await crud.search_doctors(db, specialization=specialization)
    else:
        doctors = await crud.list_all_doctors(db)

    return {
        "doctors": [
            {
                "doctor_id": d.doctor_id,
                "name": d.name,
                "specialization": d.specialization,
                "languages": d.languages_supported,
            }
            for d in doctors
        ]
    }


@app.get("/api/doctors/{doctor_id}/slots", tags=["Doctors"])
async def get_doctor_slots(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
):
    slots = await crud.get_available_slots(db, doctor_id=doctor_id, limit=20)
    return {
        "slots": [
            {
                "slot_id": s.slot_id,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "display": s.start_time.strftime("%A, %d %B %Y at %I:%M %p"),
            }
            for s in slots
        ]
    }


class BookAppointmentRequest(BaseModel):
    patient_name: str
    patient_phone: Optional[str] = None
    doctor_id: int
    slot_id: int
    reason: Optional[str] = None


@app.post("/api/appointments", tags=["Appointments"])
async def book_appointment_rest(
    req: BookAppointmentRequest,
    db: AsyncSession = Depends(get_db),
):
    patient = await crud.get_or_create_patient(
        db, name=req.patient_name, phone=req.patient_phone
    )
    try:
        appt = await crud.create_appointment(
            db,
            patient_id=patient.patient_id,
            doctor_id=req.doctor_id,
            slot_id=req.slot_id,
            reason=req.reason,
        )
        doctor = await crud.get_doctor_by_id(db, req.doctor_id)
        slot = await crud.get_slot_by_id(db, req.slot_id)
        return {
            "appointment_id": appt.appointment_id,
            "doctor_name": doctor.name if doctor else "",
            "appointment_time": slot.start_time.isoformat() if slot else "",
            "status": appt.status.value,
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/appointments/{patient_id}", tags=["Appointments"])
async def get_appointments(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
):
    appts = await crud.get_patient_appointments(db, patient_id)
    return {
        "appointments": [
            {
                "appointment_id": a.appointment_id,
                "doctor_name": a.doctor.name if a.doctor else "",
                "specialization": a.doctor.specialization if a.doctor else "",
                "time": a.slot.start_time.isoformat() if a.slot else "",
                "status": a.status.value,
            }
            for a in appts
        ]
    }


# ── Campaign Endpoints ────────────────────────────────────────────────────────


class CreateCampaignRequest(BaseModel):
    name: str
    message_template: str
    scheduled_for: str  # ISO datetime string
    patient_ids: Optional[List[int]] = None  # if None, targets all patients with upcoming appts


@app.post("/api/campaigns", tags=["Campaigns"])
async def create_campaign(
    req: CreateCampaignRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new outbound reminder campaign."""
    from datetime import datetime as dt
    from backend.database.models import Campaign, CampaignStatus
    import json

    try:
        scheduled_dt = dt.fromisoformat(req.scheduled_for)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_for datetime format. Use ISO format.")

    # Find upcoming appointments for the target patients
    if req.patient_ids:
        from sqlalchemy import select
        from backend.database.models import Appointment
        stmt = select(Appointment).where(
            Appointment.patient_id.in_(req.patient_ids),
            Appointment.status == "scheduled",
        )
        result = await db.execute(stmt)
        appts = result.scalars().all()
        appt_ids = [a.appointment_id for a in appts]
    else:
        # Target all scheduled appointments
        from sqlalchemy import select
        from backend.database.models import Appointment
        stmt = select(Appointment).where(Appointment.status == "scheduled")
        result = await db.execute(stmt)
        appts = result.scalars().all()
        appt_ids = [a.appointment_id for a in appts]

    campaign = Campaign(
        name=req.name,
        message_template=req.message_template,
        scheduled_for=scheduled_dt,
        status=CampaignStatus.PENDING,
        appointment_ids=json.dumps(appt_ids),
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    return {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "status": campaign.status.value,
        "appointment_count": len(appt_ids),
        "scheduled_for": campaign.scheduled_for.isoformat(),
    }


@app.get("/api/campaigns", tags=["Campaigns"])
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    """List all campaigns."""
    from sqlalchemy import select
    from backend.database.models import Campaign
    import json

    stmt = select(Campaign).order_by(Campaign.created_at.desc()).limit(50)
    result = await db.execute(stmt)
    campaigns = result.scalars().all()

    return {
        "campaigns": [
            {
                "campaign_id": c.campaign_id,
                "name": c.name,
                "status": c.status.value,
                "scheduled_for": c.scheduled_for.isoformat(),
                "appointment_count": len(json.loads(c.appointment_ids or "[]")),
                "created_at": c.created_at.isoformat(),
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            }
            for c in campaigns
        ]
    }


@app.post("/api/campaigns/{campaign_id}/trigger", tags=["Campaigns"])
async def trigger_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a campaign to run immediately (dispatches Celery task)."""
    from sqlalchemy import select
    from backend.database.models import Campaign, CampaignStatus

    stmt = select(Campaign).where(Campaign.campaign_id == campaign_id)
    result = await db.execute(stmt)
    campaign = result.scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.status == CampaignStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Campaign already completed")

    try:
        from workers.campaign_scheduler import run_campaign
        run_campaign.delay(campaign_id)
        return {"message": f"Campaign {campaign_id} dispatched", "campaign_id": campaign_id}
    except Exception as exc:
        # Celery may not be available — run synchronously as fallback
        logger.warning("Celery unavailable, cannot dispatch campaign", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="Campaign queuing service unavailable. Start Celery workers to enable background tasks."
        )


# ── WebSocket Endpoints ───────────────────────────────────────────────────────


@app.websocket("/ws/voice/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str):
    """
    Full-duplex real-time voice interaction endpoint.
    Accepts streaming audio and returns streaming TTS audio.
    """
    handler = VoiceWebSocketHandler(
        websocket=websocket,
        session_id=session_id,
        session_manager=app.state.session_manager,
    )
    await handler.handle()


@app.websocket("/ws/text/{session_id}")
async def text_websocket(websocket: WebSocket, session_id: str):
    """
    Text-only WebSocket endpoint — same agent, no audio.
    Used as a fallback when the user's microphone is unavailable.
    """
    # Reuse the VoiceWebSocketHandler — it handles text_message events natively
    handler = VoiceWebSocketHandler(
        websocket=websocket,
        session_id=session_id,
        session_manager=app.state.session_manager,
    )
    await handler.handle()
