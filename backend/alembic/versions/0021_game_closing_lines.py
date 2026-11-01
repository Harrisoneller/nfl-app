"""games: stored closing spread and total

closing_spread is the home margin in points (positive = home favored), the
nflverse spread_line convention. closing_total is the closing points total.
This file is the migration. It is not applied to production here.

Revision ID: 0021_game_closing_lines
Revises: 0020_settlement_status
Create Date: 2026-11-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_game_closing_lines"
down_revision: Union[str, None] = "0020_settlement_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("games", sa.Column("closing_spread", sa.Float(), nullable=True))
    op.add_column("games", sa.Column("closing_total", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "closing_total")
    op.drop_column("games", "closing_spread")
