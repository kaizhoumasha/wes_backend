"""对齐设备结果证据外键类型

Revision ID: f11b613771fa
Revises: fe7280088174
Create Date: 2026-08-25 00:21:07.947797+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f11b613771fa"
down_revision: Union[str, Sequence[str], None] = "fe7280088174"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVIDENCE_FOREIGN_KEYS = (
    ("device_commands", "result_evidence_id", True),
    ("inbound_evidence_conflicts", "first_evidence_id", False),
    ("material_executions", "last_transition_evidence_id", False),
    ("rack_replacement_transport_bindings", "source_evidence_id", False),
    ("wms_confirmations", "response_evidence_id", True),
)


def upgrade() -> None:
    """让全部 evidence 外键可保存 Snowflake 主键。"""

    for table_name, column_name, nullable in _EVIDENCE_FOREIGN_KEYS:
        op.alter_column(
            table_name,
            column_name,
            schema="wes_biz",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=nullable,
            postgresql_using=f"{column_name}::bigint",
        )


def downgrade() -> None:
    """存在超出 int32 的证据身份时拒绝破坏性降级。"""

    bind = op.get_bind()
    for table_name, column_name, _ in _EVIDENCE_FOREIGN_KEYS:
        evidence_reference = sa.table(
            table_name,
            sa.column(column_name, sa.BigInteger()),
            schema="wes_biz",
        )
        reference_column = evidence_reference.c[column_name]
        out_of_range = bind.scalar(
            sa.select(sa.exists().where(sa.or_(reference_column < -2_147_483_648, reference_column > 2_147_483_647)))
        )
        if out_of_range:
            raise RuntimeError(f"Cannot downgrade {table_name}.{column_name}: value exceeds INTEGER range")

    for table_name, column_name, nullable in _EVIDENCE_FOREIGN_KEYS:
        op.alter_column(
            table_name,
            column_name,
            schema="wes_biz",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=nullable,
            postgresql_using=f"{column_name}::integer",
        )
