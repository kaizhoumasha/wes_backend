"""widen inbound evidence workline id

Revision ID: 910b24bb0e05
Revises: 3abf401aebaa
Create Date: 2026-09-10 03:27:36.149875+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "910b24bb0e05"
down_revision: Union[str, Sequence[str], None] = "3abf401aebaa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "inbound_evidences",
        "workline_id",
        schema="wes_biz",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # PostgreSQL 拒绝无法放入 INTEGER 的既有值，禁止截断或重写工作线身份。
    op.alter_column(
        "inbound_evidences",
        "workline_id",
        schema="wes_biz",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
