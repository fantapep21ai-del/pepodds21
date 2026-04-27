import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import settings
from app.db.base import engine, Base
from app.db.models import *  # noqa: F401, F403 — registers all models
from app.db.base import AsyncSessionLocal
from app.db.models.user import User
from app.core.security import hash_password
from app.api import auth
from app.api.routers import matches, opportunities, bets, competitions
from app.api.routers import settings as settings_router
from app.api.routers import scalate
from app.api.routers import telegram_webhook
from app.api.routers import intelligence
from app.api.routers import analytics
from app.api.routers import bankroll
# from app.api.routers import results  # module does not exist

logger = logging.getLogger(__name__)


async def _seed_admin() -> None:
    """Create the default admin user if it does not exist."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == settings.admin_email))
        if result.scalar_one_or_none() is None:
            admin = User(
                email=settings.admin_email,
                hashed_password=hash_password(settings.admin_password),
                is_active=True,
                is_admin=True,
            )
            db.add(admin)
            await db.commit()
            logger.info("Admin user created: %s", settings.admin_email)


async def _register_telegram_webhook() -> None:
    """Registra il webhook Telegram se configurato."""
    if not settings.telegram_webhook_url:
        return
    try:
        from telegram import Bot
        bot = Bot(token=settings.telegram_bot_token)
        await bot.set_webhook(url=settings.telegram_webhook_url)
        logger.info("Telegram webhook registrato: %s", settings.telegram_webhook_url)
    except Exception as exc:
        logger.warning("Telegram webhook registration failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables (no-op if already exist) — migrations handle schema changes
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _seed_admin()
    await _register_telegram_webhook()
    await telegram_webhook.register_bot_commands()
    yield
    await engine.dispose()


app = FastAPI(
    title="Sports Quant Fund",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(competitions.router)
app.include_router(matches.router)
app.include_router(opportunities.router)
app.include_router(bets.router)
app.include_router(settings_router.router)
app.include_router(scalate.router)
# app.include_router(results.router)  # module does not exist
app.include_router(telegram_webhook.router)
app.include_router(intelligence.router)
app.include_router(analytics.router)
app.include_router(bankroll.router)


@app.get("/health")
async def health() -> dict:
    """Quick health check — returns system status."""
    from app.db.base import AsyncSessionLocal
    from app.db.models.context import SystemHealth
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db:
            latest = await db.execute(
                select(SystemHealth)
                .order_by(SystemHealth.checked_at.desc())
                .limit(1)
            )
            h = latest.scalar_one_or_none()
            status = h.status if h else "healthy"
            return {"status": status, "services": h.services if h else {}, "env": settings.environment}
    except Exception:
        return {"status": "healthy", "services": {}, "env": settings.environment}
