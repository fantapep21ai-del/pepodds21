from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, DateTime, Numeric, Integer, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.bet import Bet


class Scalata(Base):
    """
    Una scalata è una sequenza di N scommesse all-in pre-selezionate dal sistema.
    Si vince solo se si vincono tutti gli step. Se si perde uno step, scalata fallita.
    La puntata si raddoppia ad ogni step (all-in: stake × odds precedente).
    """
    __tablename__ = "scalate"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20), default="attiva")  # attiva | vinta | persa
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # 0=non iniziata, 1=step1 attivo, ecc.

    start_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    current_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # stake attuale
    potential_win: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))  # se vince tutti gli step

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_pnl: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    steps: Mapped[list[ScalataStep]] = relationship(
        back_populates="scalata",
        order_by="ScalataStep.step_number",
        cascade="all, delete-orphan",
    )


class ScalataStep(Base):
    """Un singolo step di una scalata."""
    __tablename__ = "scalata_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scalata_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scalate.id"), nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based

    opportunity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("betting_opportunities.id"), nullable=True)
    bet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("bets.id"), nullable=True)

    odds: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="in_attesa")  # in_attesa | attivo | vinto | perso

    match_name: Mapped[str] = mapped_column(String(300), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    match_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    placed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    scalata: Mapped[Scalata] = relationship(back_populates="steps")
