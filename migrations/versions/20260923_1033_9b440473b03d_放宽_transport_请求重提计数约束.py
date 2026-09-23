"""放宽 Transport 请求重提计数约束

Revision ID: 9b440473b03d
Revises: 1d298eb00cc4
Create Date: 2026-09-23 10:33:33.991208+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b440473b03d"
down_revision: Union[str, Sequence[str], None] = "1d298eb00cc4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """未知接收结果时，原身份可持续重提直至权威结果闭合。"""
    op.drop_constraint(
        "ck_transport_tasks_transport_submit_attempt_count_valid", "transport_tasks", schema="wes_runtime"
    )
    op.create_check_constraint(
        op.f("ck_transport_tasks_transport_submit_attempt_count_valid"),
        "transport_tasks",
        "submit_attempt_count >= 0",
        schema="wes_runtime",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_transport_tasks_transport_submit_attempt_count_valid", "transport_tasks", schema="wes_runtime"
    )
    op.create_check_constraint(
        op.f("ck_transport_tasks_transport_submit_attempt_count_valid"),
        "transport_tasks",
        "submit_attempt_count BETWEEN 0 AND 3",
        schema="wes_runtime",
    )
