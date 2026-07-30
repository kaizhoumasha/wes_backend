"""allow runtime domain effect without session

Revision ID: f557c7b749b1
Revises: 9cc0848560c6
Create Date: 2026-07-30 11:59:59.817597+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f557c7b749b1"
down_revision: Union[str, Sequence[str], None] = "9cc0848560c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """允许没有 plugin execution session 的 runtime domain EFFECT。"""

    op.alter_column(
        "runtime_intent_logs",
        "execution_session_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="wes_runtime",
    )


def downgrade() -> None:
    """恢复 plugin-only ledger 的 execution session 必填合同。"""

    op.alter_column(
        "runtime_intent_logs",
        "execution_session_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="wes_runtime",
    )
