"""sparky calibration + CLV: per-pick win_prob, clv_pct, beat_close

Revision ID: 0013_sparky_calibration_clv
Revises: 0012_sparky_sharp
Create Date: 2026-06-17

Adds three nullable columns to ``sparky_historical_results`` so settled picks
can drive the reliability/calibration curve (``win_prob`` + Brier) and the
closing-line-value scorecard (``clv_pct``, ``beat_close``). All nullable —
existing rows simply carry NULL until re-settled.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_sparky_calibration_clv"
down_revision: Union[str, None] = "0012_sparky_sharp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sparky_historical_results", sa.Column("win_prob", sa.Float(), nullable=True))
    op.add_column("sparky_historical_results", sa.Column("clv_pct", sa.Float(), nullable=True))
    op.add_column("sparky_historical_results", sa.Column("beat_close", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("sparky_historical_results", "beat_close")
    op.drop_column("sparky_historical_results", "clv_pct")
    op.drop_column("sparky_historical_results", "win_prob")
