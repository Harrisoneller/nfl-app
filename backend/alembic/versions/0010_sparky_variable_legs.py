"""sparky: variable-N parlays (2..8) + value transparency

Revision ID: 0010_sparky_variable_legs
Revises: 0009_sparky
Create Date: 2026-05-26

Changes:
  - sparky_parlay_rankings.leg3_event_id -> nullable (N can now be 2)
  - sparky_parlay_rankings + n_legs (NOT NULL, default 3 for back-compat)
  - sparky_parlay_rankings + expected_value (FLOAT, nullable)
  - sparky_parlay_rankings + kelly_fraction (FLOAT, nullable)

The ``legs`` JSONB column remains the source of truth for the full per-leg
detail; leg1/leg2/leg3 columns are legacy convenience for the first three legs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_sparky_variable_legs"
down_revision: Union[str, None] = "0009_sparky"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Allow N=2 parlays (leg3 absent).
    op.alter_column(
        "sparky_parlay_rankings",
        "leg3_event_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    # New columns. n_legs defaults to 3 so existing rows (all built as 3-leg) stay valid.
    op.add_column(
        "sparky_parlay_rankings",
        sa.Column("n_legs", sa.Integer(), nullable=False, server_default=sa.text("3")),
    )
    op.add_column(
        "sparky_parlay_rankings",
        sa.Column("expected_value", sa.Float(), nullable=True),
    )
    op.add_column(
        "sparky_parlay_rankings",
        sa.Column("kelly_fraction", sa.Float(), nullable=True),
    )
    # Drop the server_default after backfill so future inserts always specify n_legs.
    op.alter_column(
        "sparky_parlay_rankings",
        "n_legs",
        server_default=None,
        existing_type=sa.Integer(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("sparky_parlay_rankings", "kelly_fraction")
    op.drop_column("sparky_parlay_rankings", "expected_value")
    op.drop_column("sparky_parlay_rankings", "n_legs")
    op.alter_column(
        "sparky_parlay_rankings",
        "leg3_event_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
