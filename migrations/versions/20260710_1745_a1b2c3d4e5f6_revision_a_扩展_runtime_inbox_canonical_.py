"""Revision A: 扩展 runtime_inbox canonical envelope 與 hot-claim indexes

Revision ID: a1b2c3d4e5f6
Revises: f0851c5bcfdb
Create Date: 2026-07-10 17:45:00.000000+08:00

字段擴展：
- 路由/證據: kind, workline_id, device_id, command_id, trace_id, event_id, causation_id
- 內容: payload_json (JSONB), payload_schema_version
- claim: claim_bucket_key, processor_token
- 時間: received_at, processed_at, failed_at

hot-claim 索引：
- status+received_at (RECEIVED FIFO)
- status+next_retry_at (FAILED retry)
- status+lease_until (PROCESSING reclaim)
- claim_bucket_key+received_at+id (同桶 FIFO)

pre-cutover 無 payload 的行從可 claim 集合移除。

本 Revision 也保留 workline_runtime_status_projections / bin_transit_memberships
的 runtime_status 所有权（架构 guardrail 锁定），但本 PR 不修改它们的列。本字段集合
供 RuntimeInboxService.accept_received 通过 workline_id / device_id / command_id
路由查 runtime_status 时关联查询。
"""


from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f0851c5bcfdb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_SCHEMA = "wes_runtime"


def upgrade() -> None:
    """擴展 runtime_inbox 表結構.

    所有字段 nullable=True (pre-cutover audit-only 行兼容)。
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("runtime_inbox", schema=RUNTIME_SCHEMA)}

    # 1. 路由/證據字段
    if "kind" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("kind", sa.String(length=40), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "workline_id" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("workline_id", sa.Integer(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "device_id" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("device_id", sa.Integer(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "command_id" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("command_id", sa.Integer(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "trace_id" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("trace_id", sa.String(length=120), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "event_id" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("event_id", sa.String(length=120), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "causation_id" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("causation_id", sa.String(length=120), nullable=True),
            schema=RUNTIME_SCHEMA,
        )

    # 2. 內容字段
    if "payload_json" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("payload_json", sa.JSON(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "payload_schema_version" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("payload_schema_version", sa.Integer(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )

    # 3. claim 字段
    if "claim_bucket_key" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("claim_bucket_key", sa.String(length=120), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "processor_token" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("processor_token", sa.String(length=80), nullable=True),
            schema=RUNTIME_SCHEMA,
        )

    # 4. 時間字段
    if "received_at" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("received_at", sa.BigInteger(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "processed_at" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("processed_at", sa.BigInteger(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )
    if "failed_at" not in existing_columns:
        op.add_column(
            "runtime_inbox",
            sa.Column("failed_at", sa.BigInteger(), nullable=True),
            schema=RUNTIME_SCHEMA,
        )

    # 5. 索引 (hot-claim partial indexes)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("runtime_inbox", schema=RUNTIME_SCHEMA)}

    if "ix_wes_runtime_runtime_inbox_status_received" not in existing_indexes:
        op.create_index(
            "ix_wes_runtime_runtime_inbox_status_received",
            "runtime_inbox",
            ["status", "received_at"],
            schema=RUNTIME_SCHEMA,
            postgresql_where=sa.text("status = 'RECEIVED'"),
        )
    if "ix_wes_runtime_runtime_inbox_failed_retry_at" not in existing_indexes:
        op.create_index(
            "ix_wes_runtime_runtime_inbox_failed_retry_at",
            "runtime_inbox",
            ["status", "next_retry_at"],
            schema=RUNTIME_SCHEMA,
            postgresql_where=sa.text("status = 'FAILED'"),
        )
    if "ix_wes_runtime_runtime_inbox_processing_lease" not in existing_indexes:
        op.create_index(
            "ix_wes_runtime_runtime_inbox_processing_lease",
            "runtime_inbox",
            ["status", "lease_until"],
            schema=RUNTIME_SCHEMA,
            postgresql_where=sa.text("status = 'PROCESSING'"),
        )
    if "ix_wes_runtime_runtime_inbox_bucket_fifo" not in existing_indexes:
        op.create_index(
            "ix_wes_runtime_runtime_inbox_bucket_fifo",
            "runtime_inbox",
            ["claim_bucket_key", "received_at", "id"],
            schema=RUNTIME_SCHEMA,
            postgresql_where=sa.text("status IN ('RECEIVED', 'FAILED')"),
        )


def downgrade() -> None:
    """回滾（僅下線時用）。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("runtime_inbox", schema=RUNTIME_SCHEMA)}

    for idx_name in (
        "ix_wes_runtime_runtime_inbox_bucket_fifo",
        "ix_wes_runtime_runtime_inbox_processing_lease",
        "ix_wes_runtime_runtime_inbox_failed_retry_at",
        "ix_wes_runtime_runtime_inbox_status_received",
    ):
        if idx_name in existing_indexes:
            op.drop_index(idx_name, table_name="runtime_inbox", schema=RUNTIME_SCHEMA)

    for col in (
        "failed_at",
        "processed_at",
        "received_at",
        "processor_token",
        "claim_bucket_key",
        "payload_schema_version",
        "payload_json",
        "causation_id",
        "event_id",
        "trace_id",
        "command_id",
        "device_id",
        "workline_id",
        "kind",
    ):
        with op.batch_alter_table("runtime_inbox", schema=RUNTIME_SCHEMA) as batch_op:
            batch_op.drop_column(col)
