"""将软删除操作人 ID 扩为 bigint。

Revision ID: e1c64a8b9d20
Revises: bdf2d676d0a8
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1c64a8b9d20"
down_revision: Union[str, Sequence[str], None] = "bdf2d676d0a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for schema, table in (
        ("wes_biz", "devices"),
        ("wes_biz", "work_lines"),
        ("wes_sys", "api_applications"),
        ("wes_sys", "permissions"),
        ("wes_sys", "roles"),
        ("wes_sys", "users"),
    ):
        op.alter_column(
            table,
            "deleted_by",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
            schema=schema,
        )


def downgrade() -> None:
    for schema, table in (
        ("wes_biz", "devices"),
        ("wes_biz", "work_lines"),
        ("wes_sys", "api_applications"),
        ("wes_sys", "permissions"),
        ("wes_sys", "roles"),
        ("wes_sys", "users"),
    ):
        op.alter_column(
            table,
            "deleted_by",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
            schema=schema,
        )
