"""合并运行时表到业务schema

Revision ID: 510f5006d385
Revises: 334c5ca5b81d
Create Date: 2026-09-23 11:54:40.797388+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "510f5006d385"
down_revision: Union[str, Sequence[str], None] = "334c5ca5b81d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_TABLES = (
    "transport_callback_receipts",
    "transport_debug_position_projections",
    "transport_debug_run_steps",
    "transport_debug_runs",
    "transport_evidence",
    "transport_members",
    "transport_tasks",
    "workline_runtime_status_projections",
)
SCHEMA_NAMED_INDEXES = (
    "transport_evidence_transport_task_id",
    "transport_members_transport_task_id",
)


def upgrade() -> None:
    """移动现有表和附属序列、索引，保留执行证据。"""
    for table in RUNTIME_TABLES:
        op.execute(f"ALTER TABLE wes_runtime.{table} SET SCHEMA wes_biz")
    for suffix in SCHEMA_NAMED_INDEXES:
        op.execute(f"ALTER INDEX wes_biz.ix_wes_runtime_{suffix} RENAME TO ix_wes_biz_{suffix}")
    op.execute("DROP SCHEMA wes_runtime")


def downgrade() -> None:
    """恢复原有 schema 位置。"""
    op.execute("CREATE SCHEMA wes_runtime")
    for suffix in SCHEMA_NAMED_INDEXES:
        op.execute(f"ALTER INDEX wes_biz.ix_wes_biz_{suffix} RENAME TO ix_wes_runtime_{suffix}")
    for table in RUNTIME_TABLES:
        op.execute(f"ALTER TABLE wes_biz.{table} SET SCHEMA wes_runtime")
