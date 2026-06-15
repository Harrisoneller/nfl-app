"""bets: user bet tracking + CLV profile

Revision ID: 0011_bets
Revises: 0010_sparky_variable_legs
Create Date: 2026-06-14

Adds two tables:
  - ``bets``      — one logged wager (straight = 1 leg, parlay = 2+ legs)
  - ``bet_legs``  — individual selections, graded + CLV-scored at settle time

Both are scoped to a user (FK -> users.id, ON DELETE CASCADE). Money is tracked
in units with optional dollars; CLV is computed against odds_snapshots closing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_bets"
down_revision: Union[str, None] = "0010_sparky_variable_legs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bet_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=280), nullable=False),
        sa.Column("stake_units", sa.Float(), nullable=False),
        sa.Column("stake_dollars", sa.Float(), nullable=True),
        sa.Column("odds_american", sa.Integer(), nullable=False),
        sa.Column("odds_decimal", sa.Float(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payout_units", sa.Float(), nullable=True),
        sa.Column("result_units", sa.Float(), nullable=True),
        sa.Column("result_dollars", sa.Float(), nullable=True),
        sa.Column("clv_pct", sa.Float(), nullable=True),
        sa.Column("beat_close", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bets_user_placed", "bets", ["user_id", "placed_at"])
    op.create_index("ix_bets_user_status", "bets", ["user_id", "status"])

    op.create_table(
        "bet_legs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=True),
        sa.Column("game_id", sa.String(length=32), nullable=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("selection", sa.String(length=16), nullable=False),
        sa.Column("selection_label", sa.String(length=64), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("odds_american", sa.Integer(), nullable=False),
        sa.Column("odds_decimal", sa.Float(), nullable=False),
        sa.Column("home_team_id", sa.String(length=8), nullable=True),
        sa.Column("away_team_id", sa.String(length=8), nullable=True),
        sa.Column("commence_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closing_line", sa.Float(), nullable=True),
        sa.Column("closing_odds_american", sa.Integer(), nullable=True),
        sa.Column("clv_pct", sa.Float(), nullable=True),
        sa.Column("clv_line", sa.Float(), nullable=True),
        sa.Column("beat_close", sa.Boolean(), nullable=True),
        sa.Column("leg_result", sa.String(length=16), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bet_id"], ["bets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bet_legs_bet", "bet_legs", ["bet_id"])
    op.create_index("ix_bet_legs_event", "bet_legs", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_bet_legs_event", table_name="bet_legs")
    op.drop_index("ix_bet_legs_bet", table_name="bet_legs")
    op.drop_table("bet_legs")
    op.drop_index("ix_bets_user_status", table_name="bets")
    op.drop_index("ix_bets_user_placed", table_name="bets")
    op.drop_table("bets")
