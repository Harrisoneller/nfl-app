"""sparky sharp: add sharp-money summary column

Revision ID: 0012_sparky_sharp
Revises: 0011_bets
Create Date: 2026-06-17

Adds ``sparky_game_predictions.sharp`` (JSONB) — the persisted sharp-money read
for each game (sharp/retail split, sharp side, sharp steam, Pinnacle/Circa
anchor). See ``app/services/sparky/sharp.py`` (``SharpSummary``). Backfilled to
an empty object for existing rows so the dashboard renders without a rebuild.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_sparky_sharp"
down_revision: Union[str, None] = "0011_bets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sparky_game_predictions",
        sa.Column(
            "sharp",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Drop the server_default now that existing rows are populated; the ORM
    # supplies the value on every future insert (matches the other JSONB cols).
    op.alter_column("sparky_game_predictions", "sharp", server_default=None)


def downgrade() -> None:
    op.drop_column("sparky_game_predictions", "sharp")
