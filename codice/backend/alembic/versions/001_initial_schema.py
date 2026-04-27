"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-16 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("is_admin", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "competitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sport", sa.String(50), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("weight", sa.Numeric(4, 2), nullable=False, server_default="1.0"),
        sa.Column("external_id", sa.String(100)),
        sa.Column("odds_api_key", sa.String(100)),
    )

    op.create_table(
        "matches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("competition_id", UUID(as_uuid=True), sa.ForeignKey("competitions.id"), nullable=False),
        sa.Column("home_team", sa.String(200)),
        sa.Column("away_team", sa.String(200)),
        sa.Column("player_a", sa.String(200)),
        sa.Column("player_b", sa.String(200)),
        sa.Column("match_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sport", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), server_default="scheduled"),
        sa.Column("external_id", sa.String(200), unique=True),
        sa.Column("raw_stats", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_matches_sport_status", "matches", ["sport", "status"])
    op.create_index("ix_matches_match_date", "matches", ["match_date"])

    op.create_table(
        "match_odds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("match_id", UUID(as_uuid=True), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("bookmaker", sa.String(100), nullable=False),
        sa.Column("market", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("odds", sa.Numeric(8, 3), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_live", sa.Boolean(), server_default="false"),
    )
    op.create_index("ix_match_odds_match_fetched", "match_odds", ["match_id", "fetched_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("match_id", UUID(as_uuid=True), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("input_data", JSONB),
        sa.Column("output_data", JSONB),
        sa.Column("reasoning", sa.Text()),
        sa.Column("tokens_used", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "agent_votes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("match_id", UUID(as_uuid=True), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("market", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("probability", sa.Numeric(6, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 4), server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "agent_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False, unique=True),
        sa.Column("brier_score", sa.Numeric(8, 6), server_default="0.25"),
        sa.Column("total_predictions", sa.Integer(), server_default="0"),
        sa.Column("correct_predictions", sa.Integer(), server_default="0"),
        sa.Column("weight", sa.Numeric(6, 4), server_default="1.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "betting_opportunities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("match_id", UUID(as_uuid=True), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("market", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("bookmaker", sa.String(100), nullable=False),
        sa.Column("best_odds", sa.Numeric(8, 3), nullable=False),
        sa.Column("model_probability", sa.Numeric(6, 4), nullable=False),
        sa.Column("consensus_votes", JSONB),
        sa.Column("uncertainty_score", sa.Numeric(6, 4), server_default="0.0"),
        sa.Column("expected_value", sa.Numeric(8, 4), nullable=False),
        sa.Column("kelly_fraction", sa.Numeric(6, 4)),
        sa.Column("suggested_stake", sa.Numeric(10, 2)),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("uncertainty_blocked", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_opportunities_status_ev", "betting_opportunities", ["status", "expected_value"])

    op.create_table(
        "bets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("opportunity_id", UUID(as_uuid=True), sa.ForeignKey("betting_opportunities.id"), nullable=False, unique=True),
        sa.Column("bookmaker", sa.String(100), nullable=False),
        sa.Column("market", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("odds", sa.Numeric(8, 3), nullable=False),
        sa.Column("stake", sa.Numeric(10, 2), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("status", sa.String(30), server_default="open"),
        sa.Column("result", sa.String(30)),
        sa.Column("pnl", sa.Numeric(10, 2)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_bets_status_placed", "bets", ["status", "placed_at"])

    op.create_table(
        "bankroll_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("open_exposure", sa.Numeric(12, 2), server_default="0.0"),
        sa.Column("daily_pnl", sa.Numeric(10, 2)),
        sa.Column("total_bets", sa.Integer(), server_default="0"),
        sa.Column("won_bets", sa.Integer(), server_default="0"),
        sa.Column("lost_bets", sa.Integer(), server_default="0"),
        sa.Column("roi_pct", sa.Numeric(8, 4)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), server_default="running"),
        sa.Column("matches_processed", sa.Integer(), server_default="0"),
        sa.Column("opportunities_found", sa.Integer(), server_default="0"),
        sa.Column("bets_placed", sa.Integer(), server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("meta", JSONB),
    )


def downgrade() -> None:
    for t in [
        "pipeline_runs", "bankroll_snapshots", "bets",
        "betting_opportunities", "agent_scores", "agent_votes",
        "agent_runs", "match_odds", "matches", "competitions", "users",
    ]:
        op.drop_table(t)
