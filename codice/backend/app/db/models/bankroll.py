from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Numeric, Integer, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class BankrollSnapshot(Base):
    """Daily snapshot of bankroll state."""
    __tablename__ = "bankroll_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    open_exposure: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)
    daily_pnl: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    total_bets: Mapped[int] = mapped_column(Integer, default=0)
    won_bets: Mapped[int] = mapped_column(Integer, default=0)
    lost_bets: Mapped[int] = mapped_column(Integer, default=0)
    roi_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_reset_point: Mapped[bool] = mapped_column(Boolean, default=False)  # True quando l'utente imposta manualmente il bankroll
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


