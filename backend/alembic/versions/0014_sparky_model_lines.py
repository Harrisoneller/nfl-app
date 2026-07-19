"""sparky model lines: persist predicted margin + total for spread/total value

Revision ID: 0014_sparky_model_lines
Revises: 0013_sparky_calibration_clv
Create Date: 2026-06-17

Adds ``pred_margin`` and ``pred_total`` to ``sparky_game_predictions`` so the
spread and total value markets (services/sparky/markets.py) can price both
point markets off the model's point estimates. Both nullable — older rows carry
NULL until the slate is rebuilt, and the value board simply skips spread/total
for games without a model line.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_sparky_model_lines"
down_revision: Union[str, None] = "0013_sparky_calibration_clv"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sparky_game_predictions", sa.Column("pred_margin", sa.Float(), nullable=True))
    op.add_column("sparky_game_predictions", sa.Column("pred_total", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("sparky_game_predictions", "pred_total")
    op.drop_column("sparky_game_predictions", "pred_margin")
