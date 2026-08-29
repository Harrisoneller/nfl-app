"""context layer — append-only availability + team context snapshots

Phase 1 of docs/CONTEXT_LAYER_ROADMAP.md. Ports the cfb-app ``0024_context_layer``
shape into the NFL app, with NFL-specific additions (practice participation
columns for the Wed/Thu/Fri report trajectory).

Both tables are **append-only by contract** — nothing in the app updates a row
in place. That is what lets the backtest ask "what did we know when the line
was available?" instead of leaking the future into every historical game.

Revision ID: 0019_context_layer
Revises: 0018_sparky_v2
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_context_layer"
down_revision: Union[str, None] = "0018_sparky_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_availability_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("player_key", sa.String(length=128), nullable=False),
        sa.Column("player_name", sa.String(length=128), nullable=True),
        sa.Column("player_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="unknown"),
        sa.Column("practice_wed", sa.String(length=16), nullable=True),
        sa.Column("practice_thu", sa.String(length=16), nullable=True),
        sa.Column("practice_fri", sa.String(length=16), nullable=True),
        sa.Column("play_prob", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_detail", sa.String(length=64), nullable=True),
        sa.Column("evidence_url", sa.String(length=1024), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pavail_week_team", "player_availability_snapshots",
                    ["season", "week", "team_id"])
    op.create_index("ix_pavail_week_player", "player_availability_snapshots",
                    ["season", "week", "player_key"])
    op.create_index("ix_pavail_captured", "player_availability_snapshots",
                    ["captured_at"])

    op.create_table(
        "team_context_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("opponent_id", sa.String(length=32), nullable=True),
        sa.Column("component", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False,
                  server_default="derived"),
        sa.Column("value_pts", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("sigma_mult", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("evidence_url", sa.String(length=1024), nullable=True),
        sa.Column("model_version", sa.String(length=32), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tctx_week_team", "team_context_snapshots",
                    ["season", "week", "team_id"])
    op.create_index("ix_tctx_week_component", "team_context_snapshots",
                    ["season", "week", "component"])
    op.create_index("ix_tctx_captured", "team_context_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_tctx_captured", table_name="team_context_snapshots")
    op.drop_index("ix_tctx_week_component", table_name="team_context_snapshots")
    op.drop_index("ix_tctx_week_team", table_name="team_context_snapshots")
    op.drop_table("team_context_snapshots")

    op.drop_index("ix_pavail_captured", table_name="player_availability_snapshots")
    op.drop_index("ix_pavail_week_player", table_name="player_availability_snapshots")
    op.drop_index("ix_pavail_week_team", table_name="player_availability_snapshots")
    op.drop_table("player_availability_snapshots")
