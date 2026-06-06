"""sparky: odds snapshots, predictions, parlay rankings, results

Revision ID: 0009_sparky
Revises: 0008_endpoint_slo_experiments
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_sparky"
down_revision: Union[str, None] = "0008_endpoint_slo_experiments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- odds_snapshots (append-only line-movement history) ------------------
    op.create_table(
        "odds_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_label", sa.String(length=8), nullable=True),
        sa.Column("commence_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("home_team", sa.String(length=128), nullable=True),
        sa.Column("away_team", sa.String(length=128), nullable=True),
        sa.Column("home_team_id", sa.String(length=8), nullable=True),
        sa.Column("away_team_id", sa.String(length=8), nullable=True),
        sa.Column("book", sa.String(length=64), nullable=False),
        sa.Column("home_ml", sa.Integer(), nullable=True),
        sa.Column("away_ml", sa.Integer(), nullable=True),
        sa.Column("home_spread", sa.Float(), nullable=True),
        sa.Column("away_spread", sa.Float(), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("home_implied", sa.Float(), nullable=True),
        sa.Column("away_implied", sa.Float(), nullable=True),
        sa.Column("favorite", sa.String(length=8), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_odds_snap_event_captured", "odds_snapshots", ["event_id", "captured_at"])
    op.create_index("ix_odds_snap_event_book", "odds_snapshots", ["event_id", "book"])
    op.create_index("ix_odds_snap_label", "odds_snapshots", ["snapshot_label"])

    # --- sparky_game_predictions --------------------------------------------
    op.create_table(
        "sparky_game_predictions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=32), nullable=True),
        sa.Column("home_team_id", sa.String(length=8), nullable=True),
        sa.Column("away_team_id", sa.String(length=8), nullable=True),
        sa.Column("home_team", sa.String(length=128), nullable=True),
        sa.Column("away_team", sa.String(length=128), nullable=True),
        sa.Column("commence_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("predicted_winner", sa.String(length=8), nullable=True),
        sa.Column("win_prob", sa.Float(), nullable=False),
        sa.Column("model_prob", sa.Float(), nullable=True),
        sa.Column("market_prob", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("classification", sa.String(length=24), nullable=True),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("market", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slate_date", "event_id", name="uq_sparky_pred_slate_event"),
    )
    op.create_index("ix_sparky_pred_slate", "sparky_game_predictions", ["slate_date"])

    # --- sparky_parlay_rankings ---------------------------------------------
    op.create_table(
        "sparky_parlay_rankings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slate_id", sa.String(length=200), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("leg1_event_id", sa.String(length=64), nullable=False),
        sa.Column("leg2_event_id", sa.String(length=64), nullable=False),
        sa.Column("leg3_event_id", sa.String(length=64), nullable=False),
        sa.Column("leg1_pick", sa.String(length=8), nullable=True),
        sa.Column("leg2_pick", sa.String(length=8), nullable=True),
        sa.Column("leg3_pick", sa.String(length=8), nullable=True),
        sa.Column("parlay_odds_american", sa.Integer(), nullable=True),
        sa.Column("parlay_odds_decimal", sa.Float(), nullable=True),
        sa.Column("implied_prob", sa.Float(), nullable=True),
        sa.Column("combined_win_prob", sa.Float(), nullable=True),
        sa.Column("underdog_count", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("signal_alignment", sa.Float(), nullable=False),
        sa.Column("composite_score", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("legs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sparky_parlay_slate", "sparky_parlay_rankings", ["slate_id"])
    op.create_index("ix_sparky_parlay_slate_date", "sparky_parlay_rankings", ["slate_date"])

    # --- sparky_historical_results ------------------------------------------
    op.create_table(
        "sparky_historical_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=32), nullable=True),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("sport", sa.String(length=16), nullable=False),
        sa.Column("predicted_winner", sa.String(length=8), nullable=True),
        sa.Column("actual_winner", sa.String(length=8), nullable=True),
        sa.Column("prediction_correct", sa.Boolean(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("classification", sa.String(length=24), nullable=True),
        sa.Column("signal_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_sparky_result_event"),
    )
    op.create_index("ix_sparky_result_slate_date", "sparky_historical_results", ["slate_date"])

    # --- sparky_parlay_results ----------------------------------------------
    op.create_table(
        "sparky_parlay_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slate_id", sa.String(length=200), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("sport", sa.String(length=16), nullable=False),
        sa.Column("n_parlays", sa.Integer(), nullable=False),
        sa.Column("winning_combo_rank", sa.Integer(), nullable=True),
        sa.Column("rank_1_hit", sa.Boolean(), nullable=True),
        sa.Column("top_3_containment", sa.Boolean(), nullable=True),
        sa.Column("top_4_containment", sa.Boolean(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slate_id", name="uq_sparky_parlay_result_slate"),
    )
    op.create_index("ix_sparky_parlay_result_date", "sparky_parlay_results", ["slate_date"])


def downgrade() -> None:
    op.drop_index("ix_sparky_parlay_result_date", table_name="sparky_parlay_results")
    op.drop_table("sparky_parlay_results")
    op.drop_index("ix_sparky_result_slate_date", table_name="sparky_historical_results")
    op.drop_table("sparky_historical_results")
    op.drop_index("ix_sparky_parlay_slate_date", table_name="sparky_parlay_rankings")
    op.drop_index("ix_sparky_parlay_slate", table_name="sparky_parlay_rankings")
    op.drop_table("sparky_parlay_rankings")
    op.drop_index("ix_sparky_pred_slate", table_name="sparky_game_predictions")
    op.drop_table("sparky_game_predictions")
    op.drop_index("ix_odds_snap_label", table_name="odds_snapshots")
    op.drop_index("ix_odds_snap_event_book", table_name="odds_snapshots")
    op.drop_index("ix_odds_snap_event_captured", table_name="odds_snapshots")
    op.drop_table("odds_snapshots")
