"""工作线配置涉及的关联列统一为 bigint。

Revision ID: bebf575cca2b
Revises: 93deacda8c9c
Create Date: 2026-09-09 03:19:41.808496+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bebf575cca2b"
down_revision: Union[str, Sequence[str], None] = "93deacda8c9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REFERENCE_COLUMNS = (
    ("wes_biz", "devices", "work_line_id", True),
    ("wes_biz", "devices", "upstream_device_id", True),
    ("wes_biz", "material_executions", "workline_id", False),
    ("wes_runtime", "transport_tasks", "authority_workline_id", True),
    ("wes_biz", "picking_tasks", "workline_id", True),
    ("wes_biz", "position_projections", "workline_id", False),
)


def upgrade() -> None:
    """关联列与雪花主键统一为 bigint，保留现有外键和可空性。"""
    for schema, table, column, nullable in REFERENCE_COLUMNS:
        op.alter_column(
            table,
            column,
            schema=schema,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    """超出 int32 的既有数据由 PostgreSQL 拒绝降级，不截断关联 ID。"""
    for schema, table, column, nullable in reversed(REFERENCE_COLUMNS):
        op.alter_column(
            table,
            column,
            schema=schema,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=nullable,
        )
