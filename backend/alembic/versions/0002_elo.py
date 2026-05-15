"""team_elo_ratings table

Revision ID: 0002_elo
Revises: 0001_initial
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_elo"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_elo_ratings",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(8), nullable=False),
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("week", sa.Integer, nullable=False),
        sa.Column("rating", sa.Float, nullable=False, server_default="1500.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint("team_id", "season", "week", name="uq_team_elo_week"),
    )
    op.create_index("ix_team_elo_team_id", "team_elo_ratings", ["team_id"])
    op.create_index("ix_team_elo_season", "team_elo_ratings", ["season"])


def downgrade() -> None:
    op.drop_table("team_elo_ratings")
