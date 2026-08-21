"""align tree parent ids

Revision ID: db0859fd3259
Revises: 0a6378b66e1a
Create Date: 2026-08-20 18:08:04.059294+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "db0859fd3259"
down_revision: Union[str, Sequence[str], None] = "0a6378b66e1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "permissions",
        "parent_id",
        schema="wes_sys",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="parent_id::bigint",
    )
    op.alter_column(
        "menus",
        "parent_id",
        schema="wes_sys",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="parent_id::bigint",
    )


def downgrade() -> None:
    """Downgrade schema."""
    out_of_range_tables = list(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT table_name
                FROM (
                    SELECT 'wes_sys.permissions' AS table_name
                    WHERE EXISTS (
                        SELECT 1
                        FROM wes_sys.permissions
                        WHERE parent_id < -2147483648 OR parent_id > 2147483647
                    )
                    UNION ALL
                    SELECT 'wes_sys.menus' AS table_name
                    WHERE EXISTS (
                        SELECT 1
                        FROM wes_sys.menus
                        WHERE parent_id < -2147483648 OR parent_id > 2147483647
                    )
                ) AS out_of_range
                ORDER BY table_name
                """
            )
        )
        .scalars()
    )
    if out_of_range_tables:
        tables = ", ".join(out_of_range_tables)
        raise RuntimeError(f"Cannot downgrade tree parent IDs: out-of-range parent_id in {tables}")

    op.alter_column(
        "permissions",
        "parent_id",
        schema="wes_sys",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="parent_id::integer",
    )
    op.alter_column(
        "menus",
        "parent_id",
        schema="wes_sys",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="parent_id::integer",
    )
