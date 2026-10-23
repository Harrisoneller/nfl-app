"""widgets.is_public — owners can revoke the anonymous share link

Existing rows default to true so share URLs that already work keep working.
The owner sets the flag false via POST /widgets/{id}/revoke; the public
read then returns 404.

Revision ID: 0020_widget_is_public
Revises: 0019_context_layer
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_widget_is_public"
down_revision: Union[str, None] = "0019_context_layer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "widgets",
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("widgets", "is_public")
