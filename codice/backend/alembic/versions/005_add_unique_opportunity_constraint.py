"""[BUG #1 FIX] Add unique constraint on active betting opportunities.

Prevents duplicate opportunities for same (match_id, market, outcome)
when status is active. Uses PostgreSQL partial index — allows multiple
opportunities if old ones are expired/rejected.

Revision ID: 005
Revises: 004
Create Date: 2026-04-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add unique constraint on active betting opportunities."""

    # STEP 1: Rimuovi duplicati PRIMA di aggiungere constraint
    op.execute(
        """
        DELETE FROM betting_opportunities
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM betting_opportunities
            WHERE status IN ('pending', 'in_attesa', 'bet_placed')
            GROUP BY match_id, market, outcome
        )
        AND status IN ('pending', 'in_attesa', 'bet_placed');
        """
    )

    # STEP 2: Aggiungi vincolo UNIQUE parziale
    op.create_unique_constraint(
        constraint_name='uq_betting_opportunity_active',
        table_name='betting_opportunities',
        columns=['match_id', 'market', 'outcome'],
        postgresql_where="status IN ('pending', 'in_attesa', 'bet_placed')",
    )

    # STEP 3: Aggiungi indice per query performance
    op.create_index(
        index_name='ix_betting_opportunity_active_lookup',
        table_name='betting_opportunities',
        columns=['match_id', 'market', 'outcome'],
        postgresql_where="status IN ('pending', 'in_attesa', 'bet_placed')",
    )

    # STEP 4: Comment per audit
    op.execute(
        "COMMENT ON CONSTRAINT uq_betting_opportunity_active "
        "ON betting_opportunities IS "
        "'[BUG #1 FIX] Previene race condition: impossibile creare duplicati. "
        "Constraint parziale: copre solo status active.'"
    )


def downgrade() -> None:
    """Rollback: rimuovi constraint e indice."""

    op.drop_index(
        index_name='ix_betting_opportunity_active_lookup',
        table_name='betting_opportunities',
    )

    op.drop_constraint(
        constraint_name='uq_betting_opportunity_active',
        table_name='betting_opportunities',
    )
