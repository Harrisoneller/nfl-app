"""player_season_stats + team_season_aggregates

Revision ID: 0005_materialized_stats
Revises: 0004_data_sync_runs
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_materialized_stats"
down_revision: Union[str, None] = "0004_data_sync_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_season_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.String(64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(8), nullable=True),
        sa.Column("team_id", sa.String(8), nullable=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("stats", postgresql.JSONB(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "season", name="uq_player_season"),
    )
    op.create_index("ix_player_season_stats_season", "player_season_stats", ["season"])
    op.create_index("ix_player_season_stats_player_id", "player_season_stats", ["player_id"])

    op.create_table(
        "team_season_aggregates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(8), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "season", name="uq_team_season_agg"),
    )
    op.create_index("ix_team_season_aggregates_season", "team_season_aggregates", ["season"])
    op.create_index("ix_team_season_aggregates_team_id", "team_season_aggregates", ["team_id"])


def downgrade() -> None:
    op.drop_table("team_season_aggregates")
    op.drop_table("player_season_stats")
