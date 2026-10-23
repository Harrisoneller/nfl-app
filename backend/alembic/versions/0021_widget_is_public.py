"""widgets.is_public — owners can revoke the anonymous share link

Existing rows default to true so share URLs that already work keep working.
The owner sets the flag false via POST /widgets/{id}/revoke; the public
read then returns 404.

This follows #23's settlement revision. #23 lands first; #21 and #24 land
together after it.

Revision ID: 0021_widget_is_public
Revises: 0020_settlement_status
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_widget_is_public"
down_revision: Union[str, None] = "0020_settlement_status"
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
