"""drop menu persistence

Revision ID: 9624cc34fa93
Revises: d68e6be4006e
Create Date: 2026-08-26 01:09:30.528658+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9624cc34fa93"
down_revision: Union[str, Sequence[str], None] = "d68e6be4006e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.drop_table("role_menus", schema="wes_sys")
    op.drop_table("menus", schema="wes_sys")


def downgrade() -> None:
    """Downgrade schema."""

    raise RuntimeError("menu persistence removal is irreversible")
