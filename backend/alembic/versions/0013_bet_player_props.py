"""bet_legs: player-prop legs (player_name, prop_market) + wider labels

Revision ID: 0013_bet_player_props
Revises: 0012_player_props
Create Date: 2026-07-07

Adds player-prop support to the bet tracker: a leg can now carry the player it
references and the Odds API prop-market key. Grading uses the weekly stats
frame; CLV uses the append-only player_prop_snapshots history.
selection_label widens 64 → 128 (prop labels are longer than "PHI -3.5").

batch_alter_table keeps this runnable on SQLite dev databases.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_bet_player_props"
down_revision: Union[str, None] = "0012_player_props"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bet_legs") as batch:
        batch.add_column(sa.Column("player_name", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("prop_market", sa.String(length=48), nullable=True))
        batch.alter_column(
            "selection_label",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("bet_legs") as batch:
        batch.alter_column(
            "selection_label",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
        batch.drop_column("prop_market")
        batch.drop_column("player_name")
