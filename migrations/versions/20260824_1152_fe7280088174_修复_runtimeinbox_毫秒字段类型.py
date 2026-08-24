"""修复 RuntimeInbox 毫秒字段类型

Revision ID: fe7280088174
Revises: 11013119b97d
Create Date: 2026-08-24 11:52:00.794774+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe7280088174"
down_revision: Union[str, Sequence[str], None] = "11013119b97d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把联调环境中错误 stamp 为 INTEGER 的毫秒字段恢复为 BIGINT。"""

    for column_name in ("next_retry_at", "lease_until"):
        op.alter_column(
            "runtime_inbox",
            column_name,
            schema="wes_runtime",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
            postgresql_using=f"{column_name}::bigint",
        )


def downgrade() -> None:
    """父 revision 的正式合同本就是 BIGINT，降级不得重新制造错误类型。"""
