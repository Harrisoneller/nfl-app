"""player_prop_snapshots: append-only player-prop odds history

Revision ID: 0012_player_props
Revises: 0011_bets
Create Date: 2026-07-04

One row = one bookmaker's line for one player-prop market at one capture time.
Append-only (same contract as odds_snapshots) so prop line movement is
preserved for edge detection and CLV grading.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_player_props"
down_revision: Union[str, None] = "0011_bets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_prop_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commence_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("home_team_id", sa.String(length=8), nullable=True),
        sa.Column("away_team_id", sa.String(length=8), nullable=True),
        sa.Column("book", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=48), nullable=False),
        sa.Column("player_name", sa.String(length=128), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=True),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("over_price", sa.Integer(), nullable=True),
        sa.Column("under_price", sa.Integer(), nullable=True),
        sa.Column("over_implied", sa.Float(), nullable=True),
        sa.Column("under_implied", sa.Float(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prop_snap_event_captured", "player_prop_snapshots", ["event_id", "captured_at"])
    op.create_index("ix_prop_snap_player_market", "player_prop_snapshots", ["player_id", "market"])
    op.create_index("ix_prop_snap_name_market", "player_prop_snapshots", ["player_name", "market"])


def downgrade() -> None:
    op.drop_index("ix_prop_snap_name_market", table_name="player_prop_snapshots")
    op.drop_index("ix_prop_snap_player_market", table_name="player_prop_snapshots")
    op.drop_index("ix_prop_snap_event_captured", table_name="player_prop_snapshots")
    op.drop_table("player_prop_snapshots")
