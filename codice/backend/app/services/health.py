"""
System health checker — runs every 2 minutes via Celery.
Checks: DB, Redis.
Writes SystemHealth record.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def run_health_check(db) -> dict:
    """
    Full health check:
      - DB connectivity (simple query)
      - Redis ping
    Returns dict with service statuses.
    """
    from sqlalchemy import text
    from app.db.models.context import SystemHealth
    from app.config import settings

    services: dict[str, str] = {}

    # ── DB check ──────────────────────────────────────────────────────────────
    try:
        await db.execute(text("SELECT 1"))
        services["db"] = "ok"
    except Exception as exc:
        services["db"] = f"error: {exc}"
        logger.error("Health check — DB failed: %s", exc)

    # ── Redis check ───────────────────────────────────────────────────────────
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.get_redis_url())
        await r.ping()
        await r.aclose()
        services["redis"] = "ok"
    except Exception as exc:
        services["redis"] = f"error: {exc}"
        logger.warning("Health check — Redis failed: %s", exc)

    has_errors = any("error" in v for v in services.values())
    status = "degraded" if has_errors else "healthy"

    health = SystemHealth(
        checked_at=datetime.now(timezone.utc),
        status=status,
        services=services,
    )
    db.add(health)
    await db.commit()

    logger.info("Health check: status=%s services=%s", status, services)
    return {"status": status, "services": services}
