"""admin_overrides: manual projection-adjustment layer (+ merge point)

Revision ID: 0015_admin_overrides
Revises: 0013_bet_player_props, 0014_sparky_model_lines
Create Date: 2026-07-13

One row per hand-set value that supersedes a model output at read time
(game spread/total/win prob, player stat means, fantasy points, rank pins).
Deleting a row reverts to pure model output.

Also a MERGE revision: history forked at 0011_bets into the player-props
chain (0012_player_props → 0013_bet_player_props) and the sparky chain
(0012_sparky_sharp → 0013_sparky_calibration_clv → 0014_sparky_model_lines);
the sparky files were deleted in the "clean up" commit while production's
alembic_version still pointed at 0014_sparky_model_lines. The files are
restored from git history and this revision joins both heads so the graph
has a single head again.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_admin_overrides"
down_revision: Union[str, Sequence[str], None] = (
    "0013_bet_player_props",
    "0014_sparky_model_lines",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("field", sa.String(length=48), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("original_value", sa.Float(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_admin_overrides_scope", "admin_overrides",
        ["entity_type", "season", "week"],
    )
    op.create_index(
        "ix_admin_overrides_entity", "admin_overrides",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_overrides_entity", table_name="admin_overrides")
    op.drop_index("ix_admin_overrides_scope", table_name="admin_overrides")
    op.drop_table("admin_overrides")
