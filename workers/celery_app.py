"""
Celery application factory.

Broker  : Redis (same instance as session store, different DB index)
Backend : Redis

Workers are started with:
  celery -A workers.celery_app worker --loglevel=info
  celery -A workers.celery_app beat   --loglevel=info   (for scheduled tasks)
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from backend.config import settings

app = Celery(
    "voice_ai_clinic",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "workers.campaign_scheduler",
        "workers.reminder_worker",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=4,
    # Retry failed tasks up to 3 times with exponential back-off
    task_default_retry_delay=30,
    task_max_retries=3,
)

# ── Periodic beat schedule ────────────────────────────────────────────────────
app.conf.beat_schedule = {
    "dispatch-daily-reminders": {
        "task": "workers.reminder_worker.dispatch_daily_reminders",
        # Run at 09:00 UTC every day
        "schedule": crontab(hour=9, minute=0),
    },
    "cleanup-expired-sessions": {
        "task": "workers.campaign_scheduler.cleanup_stale_campaigns",
        # Run every hour
        "schedule": crontab(minute=0),
    },
}
