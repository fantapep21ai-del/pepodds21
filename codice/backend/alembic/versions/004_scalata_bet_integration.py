"""Add scalata integration to bets and actual_odds field.

Revision ID: 004
Revises: 003
Create Date: 2026-04-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── bets: scalata_id FK (per collegare bet a scalata step) ────────────────
    op.add_column('bets',
        sa.Column('scalata_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('scalate.id'), nullable=True))

    # ── bets: actual_odds (quota confermata al momento del piazzamento) ───────
    op.add_column('bets',
        sa.Column('actual_odds', sa.Numeric(8, 3), nullable=True))


def downgrade() -> None:
    op.drop_column('bets', 'actual_odds')
    op.drop_column('bets', 'scalata_id')
