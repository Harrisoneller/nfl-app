"""games: settlement claim columns

A final game moves NULL -> settling -> settled inside per-game settlement.
``settling`` is committed before the grading writes so a crash cannot leave
half-graded legs or a second historical-result insert. Those rows stay
``settling`` until ``scripts/reset_stuck_settlements.py`` resets them
(dry-run by default; it refuses production).

Revision ID: 0020_settlement_status
Revises: 0019_context_layer
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_settlement_status"
down_revision: Union[str, None] = "0019_context_layer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("settlement_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column("settlement_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column("settlement_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_games_settlement_status", "games", ["settlement_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_games_settlement_status", table_name="games")
    op.drop_column("games", "settlement_finished_at")
    op.drop_column("games", "settlement_started_at")
    op.drop_column("games", "settlement_status")
