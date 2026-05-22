"""team_metric_values + player_metric_values — indexed leaderboards

Revision ID: 0006_metric_index
Revises: 0005_materialized_stats
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_metric_index"
down_revision: Union[str, None] = "0005_materialized_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_metric_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(8), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("higher_is_better", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "season", "metric", name="uq_team_metric"),
    )
    op.create_index("ix_team_metric_values_team_id", "team_metric_values", ["team_id"])
    op.create_index("ix_team_metric_values_season", "team_metric_values", ["season"])
    op.create_index(
        "ix_team_metric_season_metric_pct",
        "team_metric_values",
        ["season", "metric", "percentile"],
    )
    op.create_index(
        "ix_team_metric_season_metric_val",
        "team_metric_values",
        ["season", "metric", "value"],
    )

    op.create_table(
        "player_metric_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.String(64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(8), nullable=True),
        sa.Column("team_id", sa.String(8), nullable=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("higher_is_better", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "season", "metric", name="uq_player_metric"),
    )
    op.create_index("ix_player_metric_values_player_id", "player_metric_values", ["player_id"])
    op.create_index("ix_player_metric_values_season", "player_metric_values", ["season"])
    op.create_index("ix_player_metric_values_position", "player_metric_values", ["position"])
    op.create_index(
        "ix_player_metric_season_pos_metric_pct",
        "player_metric_values",
        ["season", "position", "metric", "percentile"],
    )
    op.create_index(
        "ix_player_metric_season_pos_metric_val",
        "player_metric_values",
        ["season", "position", "metric", "value"],
    )


def downgrade() -> None:
    op.drop_table("player_metric_values")
    op.drop_table("team_metric_values")
