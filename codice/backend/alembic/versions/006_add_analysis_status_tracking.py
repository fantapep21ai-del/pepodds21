"""Add analysis status and reason tracking to matches.

Tracks whether each match was fully analyzed or had data gaps (incomplete/no_data).
Allows distinguishing between "complete but no value" vs "incomplete analysis".

Revision ID: 006
Revises: 005
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add analysis_status and analysis_reason columns to matches table."""

    # Add analysis_status column with default "pending"
    op.add_column(
        'matches',
        sa.Column(
            'analysis_status',
            sa.String(30),
            nullable=False,
            server_default='pending',
            comment='pending | complete | incomplete | no_data'
        )
    )

    # Add analysis_reason column (JSONB) for tracking missing data reasons
    op.add_column(
        'matches',
        sa.Column(
            'analysis_reason',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='{"type": "incomplete", "reasons": ["no_pinnacle_quotes", ...]}'
        )
    )

    # Create index for filtering by analysis status (useful for reporting)
    op.create_index(
        'ix_matches_analysis_status',
        'matches',
        ['analysis_status'],
    )


def downgrade() -> None:
    """Rollback: remove analysis tracking columns."""

    op.drop_index('ix_matches_analysis_status')
    op.drop_column('matches', 'analysis_reason')
    op.drop_column('matches', 'analysis_status')
