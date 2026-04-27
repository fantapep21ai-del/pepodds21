"""Enhanced schema: players, context, news, raw_data, system_health, composite_bets.
Add tier/edge to opportunities, closing_odds/clv to bets.

Revision ID: 003
Revises: 002
Create Date: 2026-04-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── composite_bets ────────────────────────────────────────────────────────
    op.create_table(
        'composite_bets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('bet_type', sa.String(20), nullable=False),          # double | multiple
        sa.Column('status', sa.String(30), nullable=False, server_default='open'),
        sa.Column('combined_odds', sa.Numeric(10, 4), nullable=False),
        sa.Column('combined_prob', sa.Numeric(8, 6), nullable=False),
        sa.Column('expected_value', sa.Numeric(8, 4), nullable=False),
        sa.Column('stake', sa.Numeric(10, 2), nullable=False),
        sa.Column('result', sa.String(30), nullable=True),
        sa.Column('pnl', sa.Numeric(10, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )

    # ── players ───────────────────────────────────────────────────────────────
    op.create_table(
        'players',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(300), nullable=False),
        sa.Column('sport', sa.String(50), nullable=False),
        sa.Column('team', sa.String(200), nullable=True),
        sa.Column('position', sa.String(100), nullable=True),
        sa.Column('nationality', sa.String(100), nullable=True),
        sa.Column('external_ids', postgresql.JSONB(), nullable=True),  # {api_football: X, atp_id: Y}
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_players_name', 'players', ['name'])
    op.create_index('ix_players_sport', 'players', ['sport'])

    # ── player_stats_snapshots ────────────────────────────────────────────────
    op.create_table(
        'player_stats_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('player_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('players.id'), nullable=False),
        sa.Column('match_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('matches.id'), nullable=True),
        sa.Column('snapshot_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(100), nullable=False),  # fbref | understat | darko | elo
        sa.Column('stats', postgresql.JSONB(), nullable=False),  # flexible metrics
    )
    op.create_index('ix_pss_player_date', 'player_stats_snapshots', ['player_id', 'snapshot_date'])
    op.create_index('ix_pss_source', 'player_stats_snapshots', ['source'])

    # ── news_items ────────────────────────────────────────────────────────────
    op.create_table(
        'news_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('match_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('matches.id'), nullable=True),
        sa.Column('player_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('players.id'), nullable=True),
        sa.Column('team', sa.String(200), nullable=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('source', sa.String(200), nullable=True),
        sa.Column('url', sa.String(1000), nullable=True),
        sa.Column('sentiment', sa.Numeric(4, 3), nullable=True),   # -1.0 to 1.0
        sa.Column('relevance', sa.Numeric(4, 3), nullable=True),   # 0.0 to 1.0
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_news_match', 'news_items', ['match_id'])
    op.create_index('ix_news_fetched', 'news_items', ['fetched_at'])

    # ── match_context ─────────────────────────────────────────────────────────
    op.create_table(
        'match_context',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('match_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('matches.id'), nullable=False, unique=True),
        sa.Column('lineups', postgresql.JSONB(), nullable=True),
        sa.Column('injuries', postgresql.JSONB(), nullable=True),
        sa.Column('weather', postgresql.JSONB(), nullable=True),
        sa.Column('home_form', postgresql.JSONB(), nullable=True),   # last 5 results
        sa.Column('away_form', postgresql.JSONB(), nullable=True),
        sa.Column('h2h', postgresql.JSONB(), nullable=True),         # head-to-head
        sa.Column('market_signals', postgresql.JSONB(), nullable=True),  # line movement, sharp %
        sa.Column('momentum_score', sa.Numeric(6, 4), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    # ── raw_data_store ────────────────────────────────────────────────────────
    op.create_table(
        'raw_data_store',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('source', sa.String(100), nullable=False),        # 'api_football', 'understat', etc.
        sa.Column('entity_type', sa.String(100), nullable=False),   # 'match', 'player', 'odds'
        sa.Column('entity_id', sa.String(200), nullable=True),
        sa.Column('data', postgresql.JSONB(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_raw_source_entity', 'raw_data_store', ['source', 'entity_type'])
    op.create_index('ix_raw_fetched', 'raw_data_store', ['fetched_at'])

    # ── system_health ─────────────────────────────────────────────────────────
    op.create_table(
        'system_health',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='healthy'),  # healthy|degraded|down
        sa.Column('readonly_mode', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('drawdown_pct', sa.Numeric(6, 4), nullable=True),
        sa.Column('services', postgresql.JSONB(), nullable=True),  # {db: ok, redis: ok, ...}
        sa.Column('notes', sa.Text(), nullable=True),
    )

    # ── Update betting_opportunities: tier, edge, composite_bet_id ────────────
    op.add_column('betting_opportunities',
        sa.Column('tier', sa.String(5), nullable=False, server_default='C'))
    op.add_column('betting_opportunities',
        sa.Column('edge', sa.Numeric(8, 6), nullable=True))          # EV * confidence
    op.add_column('betting_opportunities',
        sa.Column('composite_bet_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('composite_bets.id'), nullable=True))

    # ── Update bets: closing_odds, clv, composite_bet_id ─────────────────────
    op.add_column('bets',
        sa.Column('closing_odds', sa.Numeric(8, 3), nullable=True))  # closing line odds
    op.add_column('bets',
        sa.Column('clv', sa.Numeric(8, 4), nullable=True))           # closing line value %
    op.add_column('bets',
        sa.Column('composite_bet_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('composite_bets.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('bets', 'composite_bet_id')
    op.drop_column('bets', 'clv')
    op.drop_column('bets', 'closing_odds')
    op.drop_column('betting_opportunities', 'composite_bet_id')
    op.drop_column('betting_opportunities', 'edge')
    op.drop_column('betting_opportunities', 'tier')
    op.drop_table('system_health')
    op.drop_table('raw_data_store')
    op.drop_table('match_context')
    op.drop_table('news_items')
    op.drop_table('player_stats_snapshots')
    op.drop_table('players')
    op.drop_table('composite_bets')
