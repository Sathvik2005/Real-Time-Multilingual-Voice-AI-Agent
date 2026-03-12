"""
Daily appointment reminder worker.

Finds all appointments scheduled for tomorrow, creates/updates a campaign
record, and dispatches individual reminder tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from workers.celery_app import app as celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="workers.reminder_worker.dispatch_daily_reminders")
def dispatch_daily_reminders() -> dict:
    result = asyncio.get_event_loop().run_until_complete(_dispatch_async())
    return result


async def _dispatch_async() -> dict:
    from sqlalchemy import and_, select

    from backend.database.connection import get_session_factory
    from backend.database.models import Appointment, AppointmentStatus, Campaign, Slot

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tomorrow_start = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    tomorrow_end = tomorrow_start.replace(hour=23, minute=59, second=59)

    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(Appointment)
            .join(Slot)
            .where(
                and_(
                    Appointment.status == AppointmentStatus.SCHEDULED,
                    Slot.start_time >= tomorrow_start,
                    Slot.start_time <= tomorrow_end,
                )
            )
        )
        appointments = result.scalars().all()

        if not appointments:
            logger.info("No appointments to remind tomorrow")
            return {"reminders_scheduled": 0}

        appt_ids = [a.appointment_id for a in appointments]

        # Create campaign record
        campaign = Campaign(
            name=f"Daily Reminder {now.strftime('%Y-%m-%d')}",
            message_template=(
                "Hello {patient_name}, this is a reminder from your clinic. "
                "You have an appointment with Dr. {doctor_name} tomorrow at {appointment_time}. "
                "Please reply to confirm, reschedule, or cancel."
            ),
            scheduled_for=now,
            appointment_ids=json.dumps(appt_ids),
        )
        db.add(campaign)
        await db.flush()
        campaign_id = campaign.campaign_id
        await db.commit()

    # Dispatch the campaign task
    from workers.campaign_scheduler import run_campaign
    run_campaign.delay(campaign_id)

    logger.info(
        "Reminder campaign dispatched",
        campaign_id=campaign_id,
        appointment_count=len(appt_ids),
    )
    return {"campaign_id": campaign_id, "reminders_scheduled": len(appt_ids)}
