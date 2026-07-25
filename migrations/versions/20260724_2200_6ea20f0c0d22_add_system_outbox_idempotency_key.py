"""add system outbox idempotency key

Revision ID: 6ea20f0c0d22
Revises: bba1942e9ea8
Create Date: 2026-07-24 22:00:30.201151+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ea20f0c0d22"
down_revision: Union[str, Sequence[str], None] = "bba1942e9ea8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为下游 EFFECT 请求增加兼容历史记录的可空幂等键。"""

    op.add_column(
        "system_outbox",
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除下游 EFFECT 请求幂等键。"""

    op.drop_column("system_outbox", "idempotency_key", schema="wes_biz")
