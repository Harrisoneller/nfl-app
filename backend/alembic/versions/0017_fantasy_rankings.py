"""Custom fantasy ranking sets: ranking_sets + ranking_entries.

Revision ID: 0017_fantasy_rankings
Revises: 0016_model_params
Create Date: 2026-07-20

ranking_sets    — admin-authored, format-tagged big boards (draft → publish
                  snapshot in published_json; public reads snapshot only).
ranking_entries — working-draft rows: one player, one rank, one tier per set.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_fantasy_rankings"
down_revision: Union[str, Sequence[str], None] = "0016_model_params"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "ranking_sets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False, server_default="custom"),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_json", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        *_timestamps(),
        sa.UniqueConstraint("season", "name", name="uq_ranking_sets_season_name"),
    )
    op.create_index(
        "ix_ranking_sets_season_status", "ranking_sets", ["season", "status"],
    )

    op.create_table(
        "ranking_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "set_id", sa.Integer(),
            sa.ForeignKey("ranking_sets.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("player_id", sa.String(length=64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        *_timestamps(),
        sa.UniqueConstraint("set_id", "player_id", name="uq_ranking_entries_set_player"),
    )
    op.create_index(
        "ix_ranking_entries_set_rank", "ranking_entries", ["set_id", "rank"],
    )


def downgrade() -> None:
    op.drop_index("ix_ranking_entries_set_rank", table_name="ranking_entries")
    op.drop_table("ranking_entries")
    op.drop_index("ix_ranking_sets_season_status", table_name="ranking_sets")
    op.drop_table("ranking_sets")
