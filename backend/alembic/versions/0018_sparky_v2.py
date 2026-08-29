"""sparky v2 — parlay receipts, model/market probs, season/week on predictions

Ports CFB migrations 0023_sparky_parlay_v2 and 0025_sparky_week into the NFL
app as a single revision.

* ``sparky_parlay_rankings`` — the numbers the rebuilt parlay engine actually
  ranks on (``ev_adjusted``, ``growth_rate``, correlation/push receipts).
* ``sparky_historical_results`` — ``model_prob`` / ``market_prob`` so the
  edge-shrinkage factor is fittable from settled picks.
* ``sparky_game_predictions`` — ``season`` / ``week`` so the board filters to
  an NFL week (Thu–Mon, plus flex/international) rather than a rolling window.

``sharp``, ``pred_margin``, ``pred_total``, ``win_prob``, ``clv_pct`` and
``beat_close`` already exist from 0012–0014; this revision does not touch them.
The ORM now maps those columns (it did not before).

Revision ID: 0018_sparky_v2
Revises: 0017_fantasy_rankings
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_sparky_v2"
down_revision: Union[str, None] = "0017_fantasy_rankings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RANKING_COLS = (
    ("ev_adjusted", sa.Float()),
    ("selection_penalty", sa.Float()),
    ("growth_rate", sa.Float()),
    ("hit_prob", sa.Float()),
    ("correlation_effect", sa.Float()),
    ("push_effect", sa.Float()),
)

_RESULT_COLS = (
    ("model_prob", sa.Float()),
    ("market_prob", sa.Float()),
)


def upgrade() -> None:
    for name, type_ in _RANKING_COLS:
        op.add_column("sparky_parlay_rankings", sa.Column(name, type_, nullable=True))
    op.add_column(
        "sparky_parlay_rankings",
        sa.Column("exact_priced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "sparky_parlay_rankings",
        sa.Column(
            "pricing", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
    )
    for name, type_ in _RESULT_COLS:
        op.add_column("sparky_historical_results", sa.Column(name, type_, nullable=True))

    op.add_column(
        "sparky_game_predictions",
        sa.Column("season", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sparky_game_predictions",
        sa.Column("week", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_sparky_pred_season_week",
        "sparky_game_predictions",
        ["season", "week"],
    )


def downgrade() -> None:
    op.drop_index("ix_sparky_pred_season_week", table_name="sparky_game_predictions")
    op.drop_column("sparky_game_predictions", "week")
    op.drop_column("sparky_game_predictions", "season")
    for name, _ in _RESULT_COLS:
        op.drop_column("sparky_historical_results", name)
    op.drop_column("sparky_parlay_rankings", "pricing")
    op.drop_column("sparky_parlay_rankings", "exact_priced")
    for name, _ in reversed(_RANKING_COLS):
        op.drop_column("sparky_parlay_rankings", name)
