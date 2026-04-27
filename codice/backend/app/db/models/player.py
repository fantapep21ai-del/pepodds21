from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.match import Match


class Player(Base):
    """Player profile — enriched from multiple data sources."""
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    team: Mapped[Optional[str]] = mapped_column(String(200))
    position: Mapped[Optional[str]] = mapped_column(String(100))
    nationality: Mapped[Optional[str]] = mapped_column(String(100))
    # external_ids: {"api_football": "12345", "atp_id": "federer", "fbref_id": "xyz"}
    external_ids: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    stats_snapshots: Mapped[list[PlayerStatsSnapshot]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


class PlayerStatsSnapshot(Base):
    """
    Point-in-time stats snapshot for a player.
    stats JSONB holds any source-specific metrics:
      FBref:      xG, xAG, progressive_carries, ...
      Understat:  xG_rolling_5, npxG, ...
      DARKO:      DPM, o_dpm, d_dpm, ...
      Elo:        elo_rating, surface_elo (tennis), ...
      Tennis:     MTR, dominance_ratio, pressure_rate, ...
    """
    __tablename__ = "player_stats_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("players.id"), nullable=False
    )
    match_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=True
    )
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)   # fbref | understat | darko | elo
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False)

    player: Mapped[Player] = relationship(back_populates="stats_snapshots")
