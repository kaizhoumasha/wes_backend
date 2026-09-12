"""remove transport resource ownership

Revision ID: fa4f7c135230
Revises: 379a204b41fa
Create Date: 2026-09-12 12:56:48.747637+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa4f7c135230"
down_revision: Union[str, Sequence[str], None] = "379a204b41fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除跨任务物理占用 owner；任务成员与结果历史继续保留。"""
    op.drop_table("transport_resource_bindings", schema="wes_runtime")


def downgrade() -> None:
    """只恢复旧结构，不推测占用或删除任务事实来满足旧约束。"""
    op.create_table(
        "transport_resource_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=10), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["transport_task_id"],
            ["wes_runtime.transport_tasks.transport_task_id"],
            name=op.f("fk_transport_resource_bindings_transport_task_id_transport_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_resource_bindings")),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_resource_bindings_task",
        "transport_resource_bindings",
        ["transport_task_id", "released_at"],
        schema="wes_runtime",
    )
    op.create_index(
        "ux_transport_resource_bindings_active",
        "transport_resource_bindings",
        ["resource_type", "resource_id"],
        unique=True,
        schema="wes_runtime",
        postgresql_where=sa.text("released_at IS NULL"),
    )
