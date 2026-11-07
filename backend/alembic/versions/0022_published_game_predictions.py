"""published game predictions

One stored qb-ridge-v5 payload per game id, plus a single-row refresh lease.
Additive tables only. This file is the migration. It is not applied here.

Revision ID: 0022_published_game_predictions
Revises: 0021_game_closing_lines
Create Date: 2026-11-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_published_game_predictions"
down_revision: str | None = "0021_game_closing_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "published_game_predictions",
        sa.Column("game_id", sa.String(length=64), primary_key=True),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("home_team_id", sa.String(length=8), nullable=True),
        sa.Column("away_team_id", sa.String(length=8), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("input_revision", sa.String(length=64), nullable=False),
        sa.Column("headline_pipeline", sa.String(length=64), nullable=False),
        sa.Column("prediction_basis", sa.String(length=64), nullable=True),
        sa.Column("payload", _JSON, nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kickoff_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_published_pred_season_week",
        "published_game_predictions",
        ["season", "week"],
    )
    op.create_index(
        "ix_published_pred_revision",
        "published_game_predictions",
        ["input_revision"],
    )
    op.create_table(
        "prediction_refresh_leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("resume_after", sa.String(length=64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("prediction_refresh_leases")
    op.drop_index("ix_published_pred_revision", table_name="published_game_predictions")
    op.drop_index("ix_published_pred_season_week", table_name="published_game_predictions")
    op.drop_table("published_game_predictions")
