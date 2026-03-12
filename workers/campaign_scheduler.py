"""
Outbound campaign scheduler.

A campaign targets a set of appointments and sends reminder messages
through the voice/text pipeline.  Each campaign is stored in the
``campaigns`` table and processed by a Celery task.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from workers.celery_app import app as celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="workers.campaign_scheduler.run_campaign")
def run_campaign(self, campaign_id: int) -> dict:
    """
    Execute an outbound reminder campaign.

    This task is idempotent — re-running it for a completed campaign
    is a no-op due to the status check.
    """
    try:
        result = asyncio.get_event_loop().run_until_complete(
            _run_campaign_async(campaign_id)
        )
        return result
    except Exception as exc:
        logger.error("Campaign %d failed: %s", campaign_id, exc)
        raise self.retry(exc=exc)


async def _run_campaign_async(campaign_id: int) -> dict:
    from sqlalchemy import select, update

    from backend.database.connection import get_session_factory
    from backend.database.models import Appointment, Campaign, CampaignStatus, Patient
    from backend.database.models import Doctor, Slot

    factory = get_session_factory()
    async with factory() as db:
        # Load campaign
        result = await db.execute(
            select(Campaign).where(Campaign.campaign_id == campaign_id)
        )
        campaign = result.scalar_one_or_none()

        if not campaign:
            return {"error": f"Campaign {campaign_id} not found"}

        if campaign.status not in (CampaignStatus.PENDING, CampaignStatus.RUNNING):
            return {"skipped": True, "status": campaign.status.value}

        # Mark as RUNNING
        await db.execute(
            update(Campaign)
            .where(Campaign.campaign_id == campaign_id)
            .values(status=CampaignStatus.RUNNING)
        )
        await db.commit()

        appointment_ids: List[int] = json.loads(campaign.appointment_ids or "[]")
        sent = 0

        for appt_id in appointment_ids:
            result = await db.execute(
                select(Appointment)
                .join(Patient)
                .join(Slot)
                .join(Doctor)
                .where(Appointment.appointment_id == appt_id)
            )
            appt = result.scalar_one_or_none()
            if not appt:
                continue

            # Build reminder message from template
            message = campaign.message_template.format(
                patient_name=appt.patient.name if appt.patient else "Patient",
                doctor_name=appt.doctor.name if appt.doctor else "Doctor",
                appointment_time=(
                    appt.slot.start_time.strftime("%A, %d %B at %I:%M %p")
                    if appt.slot
                    else "your scheduled time"
                ),
            )

            # In a real deployment this would call the TTS pipeline and
            # dial the patient via a telephony API (Twilio, AWS Connect, etc.)
            logger.info(
                "Reminder dispatched [SIMULATED]",
                campaign=campaign.name,
                appointment_id=appt_id,
                patient=appt.patient.name if appt.patient else "?",
                message=message[:80],
            )
            sent += 1

        # Mark as COMPLETED
        await db.execute(
            update(Campaign)
            .where(Campaign.campaign_id == campaign_id)
            .values(
                status=CampaignStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await db.commit()

    return {"campaign_id": campaign_id, "reminders_sent": sent}


@celery_app.task(name="workers.campaign_scheduler.cleanup_stale_campaigns")
def cleanup_stale_campaigns() -> dict:
    """Mark campaigns stuck in RUNNING state for > 2 hours as FAILED."""
    result = asyncio.get_event_loop().run_until_complete(_cleanup_async())
    return result


async def _cleanup_async() -> dict:
    from sqlalchemy import select, update

    from backend.database.connection import get_session_factory
    from backend.database.models import Campaign, CampaignStatus

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    factory = get_session_factory()

    async with factory() as db:
        result = await db.execute(
            select(Campaign).where(
                Campaign.status == CampaignStatus.RUNNING,
                Campaign.scheduled_for < cutoff,
            )
        )
        stale = result.scalars().all()

        for c in stale:
            c.status = CampaignStatus.FAILED
            logger.warning("Marking stale campaign as FAILED", campaign_id=c.campaign_id)

        await db.commit()

    return {"stale_campaigns_cleaned": len(stale)}
