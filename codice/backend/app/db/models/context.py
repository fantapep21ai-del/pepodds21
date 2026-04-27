from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class MatchContext(Base):
    """
    Rich contextual data for a match — assembled from multiple sources.
    One record per match, updated before each analysis run.

    lineups:       {"home": ["PlayerA", ...], "away": [...]}
    injuries:      {"home": [{"player": "X", "status": "out"}], "away": [...]}
    weather:       {"temp_c": 12, "wind_kmh": 20, "condition": "rain"}
    home_form:     [{"date": "...", "result": "W", "score": "2-1"}, ...]
    away_form:     same structure
    h2h:           {"matches": [...], "home_wins": 3, "away_wins": 2, "draws": 1}
    market_signals: {
        "opening_odds": 2.10, "current_odds": 1.95,
        "line_movement": -0.15, "sharp_side": "home",
        "public_pct": 65, "sharp_pct": 58
    }
    momentum_score: float (-1.0 to 1.0) — composite momentum indicator
    """
    __tablename__ = "match_context"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False, unique=True
    )

    lineups: Mapped[Optional[dict]] = mapped_column(JSONB)
    injuries: Mapped[Optional[dict]] = mapped_column(JSONB)
    weather: Mapped[Optional[dict]] = mapped_column(JSONB)
    home_form: Mapped[Optional[dict]] = mapped_column(JSONB)
    away_form: Mapped[Optional[dict]] = mapped_column(JSONB)
    h2h: Mapped[Optional[dict]] = mapped_column(JSONB)
    market_signals: Mapped[Optional[dict]] = mapped_column(JSONB)
    momentum_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RawDataStore(Base):
    """
    Archive of every raw API response before any processing.
    Guarantees full auditability and allows reprocessing.
    """
    __tablename__ = "raw_data_store"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(100), nullable=False)       # api_football | understat | odds_api
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)  # match | player | odds | news
    entity_id: Mapped[Optional[str]] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class SystemHealth(Base):
    """System health snapshot — written every 2 minutes."""
    __tablename__ = "system_health"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="healthy")  # healthy | degraded | down
    services: Mapped[Optional[dict]] = mapped_column(JSONB)  # {db: "ok", redis: "ok"}
    notes: Mapped[Optional[str]] = mapped_column(String(500))
