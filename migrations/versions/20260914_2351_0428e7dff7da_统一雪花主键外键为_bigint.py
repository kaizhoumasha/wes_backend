"""统一雪花主键外键为 BIGINT

Revision ID: 0428e7dff7da
Revises: 70d00a14cbdf
Create Date: 2026-09-14 23:51:33.166437+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0428e7dff7da"
down_revision: Union[str, Sequence[str], None] = "70d00a14cbdf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REFERENCE_COLUMNS = (
    ("inbound_evidences", "material_execution_id", True),
    ("wms_confirmations", "material_execution_id", True),
    ("device_commands", "material_execution_id", True),
    ("workline_sessions", "workline_id", False),
    ("workline_timelines", "session_id", False),
    ("workline_timelines", "workline_id", False),
    ("workline_timelines", "related_command_id", True),
)


def upgrade() -> None:
    for table, column, nullable in REFERENCE_COLUMNS:
        op.alter_column(
            table,
            column,
            schema="wes_biz",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    # 超出 int32 的既有身份由 PostgreSQL 拒绝降级，不截断关联 ID。
    for table, column, nullable in reversed(REFERENCE_COLUMNS):
        op.alter_column(
            table,
            column,
            schema="wes_biz",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=nullable,
        )
