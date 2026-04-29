from celery import Celery
from celery.schedules import crontab
from datetime import timedelta
from app.config import settings

celery_app = Celery(
    "sports_quant_fund",
    broker=settings.redis_url_with_auth,
    backend=settings.redis_url_with_auth,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
)

# ── Beat schedule ──────────────────────────────────────────────────────────────
# NOTA: Fetch (odds, stats, competizioni) sono on-demand via comandi Telegram.
# Rimangono SOLO task di servizio e monitoring (health-check, polling).
celery_app.conf.beat_schedule = {

    # ── Health check ogni 5 minuti ────────────────────────────────────────────
    "health-check": {
        "task": "app.workers.tasks.run_health_check",
        "schedule": crontab(minute="*/5"),
    },

    # ── Settlement ogni 2 ore (scommesse finite) ──────────────────────────────
    "controlla": {
        "task": "app.workers.tasks.controlla",
        "schedule": crontab(minute=0, hour="*/2"),
    },

    # ── CLV update ogni 6 ore ─────────────────────────────────────────────────
    "update-clv": {
        "task": "app.workers.tasks.update_clv",
        "schedule": crontab(minute=0, hour="*/6"),
    },

    # ── CLV calibration ogni lunedì alle 08:00 UTC ────────────────────────────
    "calibrate-clv-weekly": {
        "task": "app.workers.tasks.calibrate_clv",
        "schedule": crontab(hour=8, minute=0, day_of_week=1),
    },
}
