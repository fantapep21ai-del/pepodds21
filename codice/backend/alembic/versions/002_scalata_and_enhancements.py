"""Add scalata tables and enhance opportunities + bankroll models.

Revision ID: 002
Revises: 001
Create Date: 2026-04-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── scalate table ──────────────────────────────────────────────────────────
    op.create_table(
        'scalate',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='attiva'),
        sa.Column('total_steps', sa.Integer(), nullable=False),
        sa.Column('current_step', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('start_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('current_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('potential_win', sa.Numeric(10, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_pnl', sa.Numeric(10, 2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )

    # ── scalata_steps table ───────────────────────────────────────────────────
    op.create_table(
        'scalata_steps',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('scalata_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('scalate.id'), nullable=False),
        sa.Column('step_number', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('betting_opportunities.id'), nullable=True),
        sa.Column('bet_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('bets.id'), nullable=True),
        sa.Column('odds', sa.Numeric(8, 3), nullable=False),
        sa.Column('stake', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='in_attesa'),
        sa.Column('match_name', sa.String(300), nullable=False),
        sa.Column('market', sa.String(100), nullable=False),
        sa.Column('outcome', sa.String(200), nullable=False),
        sa.Column('bookmaker', sa.String(100), nullable=False),
        sa.Column('match_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('placed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── betting_opportunities: nuovi campi ────────────────────────────────────
    op.add_column('betting_opportunities',
        sa.Column('bet_type', sa.String(30), nullable=False, server_default='singola'))
    op.add_column('betting_opportunities',
        sa.Column('confidence_level', sa.String(20), nullable=False, server_default='normale'))
    op.add_column('betting_opportunities',
        sa.Column('scalata_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('scalate.id'), nullable=True))
    op.add_column('betting_opportunities',
        sa.Column('scalata_step', sa.Integer(), nullable=True))

    # ── bankroll_snapshots: campo reset point ─────────────────────────────────
    op.add_column('bankroll_snapshots',
        sa.Column('is_reset_point', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('bankroll_snapshots', 'is_reset_point')
    op.drop_column('betting_opportunities', 'scalata_step')
    op.drop_column('betting_opportunities', 'scalata_id')
    op.drop_column('betting_opportunities', 'confidence_level')
    op.drop_column('betting_opportunities', 'bet_type')
    op.drop_table('scalata_steps')
    op.drop_table('scalate')
