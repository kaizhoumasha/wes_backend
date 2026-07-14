"""create runtime inbox indexes concurrently

Revision ID: e0d58415afc9
Revises: ec426c628516
Create Date: 2026-07-14 11:03:19.208234+08:00

Revision C intentionally separates index construction from the Revision A/B
data migrations so PostgreSQL never takes a blocking hot-table index build.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0d58415afc9"
down_revision: Union[str, Sequence[str], None] = "ec426c628516"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_SCHEMA = "wes_runtime"

_INDEXES: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    ("ix_wes_runtime_runtime_inbox_kind", ("kind",), None),
    ("ix_wes_runtime_runtime_inbox_workline_id", ("workline_id",), None),
    ("ix_wes_runtime_runtime_inbox_device_id", ("device_id",), None),
    ("ix_wes_runtime_runtime_inbox_command_id", ("command_id",), None),
    ("ix_wes_runtime_runtime_inbox_trace_id", ("trace_id",), None),
    ("ix_wes_runtime_runtime_inbox_claim_bucket_key", ("claim_bucket_key",), None),
    ("ix_wes_runtime_runtime_inbox_status_received", ("status", "received_at"), "status = 'RECEIVED'"),
    ("ix_wes_runtime_runtime_inbox_failed_retry_at", ("status", "next_retry_at"), "status = 'FAILED'"),
    ("ix_wes_runtime_runtime_inbox_processing_lease", ("status", "lease_until"), "status = 'PROCESSING'"),
    (
        "ix_wes_runtime_runtime_inbox_bucket_fifo",
        ("claim_bucket_key", "received_at", "id"),
        "status IN ('RECEIVED', 'FAILED')",
    ),
    ("ix_wes_runtime_runtime_inbox_workline_session_id", ("workline_session_id",), None),
)


def _runtime_inbox_index_states() -> dict[str, tuple[bool, bool]]:
    """读取索引是否 valid/ready，避免把失败遗留的同名索引误判为完成。"""

    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT index_row.relname AS index_name,
                   index_metadata.indisvalid,
                   index_metadata.indisready
            FROM pg_catalog.pg_index AS index_metadata
            JOIN pg_catalog.pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
            JOIN pg_catalog.pg_class AS table_row ON table_row.oid = index_metadata.indrelid
            JOIN pg_catalog.pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            WHERE namespace_row.nspname = :schema_name
              AND table_row.relname = 'runtime_inbox'
            """
        ),
        {"schema_name": RUNTIME_SCHEMA},
    )
    return {
        str(row.index_name): (bool(row.indisvalid), bool(row.indisready))
        for row in rows
    }


def upgrade() -> None:
    """在事务外并发创建 RuntimeInbox 路由与 claim 索引。"""

    with op.get_context().autocommit_block():
        index_states = _runtime_inbox_index_states()
        for name, columns, predicate in _INDEXES:
            state = index_states.get(name)
            if state == (True, True):
                continue
            if state is not None:
                op.drop_index(
                    name,
                    table_name="runtime_inbox",
                    schema=RUNTIME_SCHEMA,
                    postgresql_concurrently=True,
                )
            op.create_index(
                name,
                "runtime_inbox",
                list(columns),
                schema=RUNTIME_SCHEMA,
                postgresql_where=sa.text(predicate) if predicate is not None else None,
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    """在事务外并发删除本 revision 管理的索引。"""

    with op.get_context().autocommit_block():
        existing_indexes = _runtime_inbox_index_states().keys()
        for name, _columns, _predicate in reversed(_INDEXES):
            if name in existing_indexes:
                op.drop_index(
                    name,
                    table_name="runtime_inbox",
                    schema=RUNTIME_SCHEMA,
                    postgresql_concurrently=True,
                )
