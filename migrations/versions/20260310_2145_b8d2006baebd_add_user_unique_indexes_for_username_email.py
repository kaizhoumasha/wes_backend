"""add partial unique indexes for user username and email

Revision ID: b8d2006baebd
Revises: 7f2a9c1b4e6d
Create Date: 2026-03-10 21:45:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d2006baebd"
down_revision: Union[str, Sequence[str], None] = "7f2a9c1b4e6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ux_users_username_deleted",
        "users",
        ["username"],
        unique=True,
        schema="wes_sys",
        postgresql_where="NOT is_deleted",
    )
    op.create_index(
        "ux_users_email_deleted",
        "users",
        ["email"],
        unique=True,
        schema="wes_sys",
        postgresql_where="NOT is_deleted",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ux_users_email_deleted",
        table_name="users",
        schema="wes_sys",
        postgresql_where="NOT is_deleted",
    )
    op.drop_index(
        "ux_users_username_deleted",
        table_name="users",
        schema="wes_sys",
        postgresql_where="NOT is_deleted",
    )
