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
# Task periodici essenziali per il sistema
celery_app.conf.beat_schedule = {
    "sync_competitions_daily": {
        "task": "app.workers.tasks.sync_competitions",
        "schedule": crontab(hour=9, minute=42),  # Tra ~1 minuto per caricare competizioni
    },
}
# Nota: Ricerche sono ON-DEMAND via Telegram commands SOLAMENTE — nessun task automatico per fetch/pipeline
