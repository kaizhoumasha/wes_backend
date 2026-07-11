"""extend runtime inbox

Revision ID: b8a28e1bfec8
Revises: f0851c5bcfdb
Create Date: 2026-07-11 18:15:25.064764+08:00

本迁移保留 workline_runtime_status_projections 与 bin_transit_memberships
的 runtime_status 所有权，不修改两者结构。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8a28e1bfec8"
down_revision: Union[str, Sequence[str], None] = "f0851c5bcfdb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_SCHEMA = "wes_runtime"


_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("kind", sa.String(length=40), nullable=True),
    sa.Column("workline_id", sa.Integer(), nullable=True),
    sa.Column("device_id", sa.Integer(), nullable=True),
    sa.Column("command_id", sa.Integer(), nullable=True),
    sa.Column("trace_id", sa.String(length=120), nullable=True),
    sa.Column("event_id", sa.String(length=120), nullable=True),
    sa.Column("causation_id", sa.String(length=120), nullable=True),
    sa.Column("payload_json", sa.JSON(), nullable=True),
    sa.Column("payload_schema_version", sa.Integer(), nullable=True),
    sa.Column("claim_bucket_key", sa.String(length=120), nullable=True),
    sa.Column("processor_token", sa.String(length=80), nullable=True),
    sa.Column("received_at", sa.BigInteger(), nullable=True),
    sa.Column("processed_at", sa.BigInteger(), nullable=True),
    sa.Column("failed_at", sa.BigInteger(), nullable=True),
)

_MILLISECOND_COLUMNS = ("next_retry_at", "lease_until")

_INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("ix_wes_runtime_runtime_inbox_status_received", ("status", "received_at"), "status = 'RECEIVED'"),
    ("ix_wes_runtime_runtime_inbox_failed_retry_at", ("status", "next_retry_at"), "status = 'FAILED'"),
    ("ix_wes_runtime_runtime_inbox_processing_lease", ("status", "lease_until"), "status = 'PROCESSING'"),
    (
        "ix_wes_runtime_runtime_inbox_bucket_fifo",
        ("claim_bucket_key", "received_at", "id"),
        "status IN ('RECEIVED', 'FAILED')",
    ),
)


def upgrade() -> None:
    """扩展 canonical envelope、claim 字段与 hot-claim indexes。"""
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for column_name in _MILLISECOND_COLUMNS:
        if column_name in existing_columns:
            op.alter_column(
                "runtime_inbox",
                column_name,
                schema=RUNTIME_SCHEMA,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=True,
            )
    for column in _COLUMNS:
        if column.name not in existing_columns:
            op.add_column("runtime_inbox", column, schema=RUNTIME_SCHEMA)

    inspector = sa.inspect(op.get_bind())
    existing_indexes = {index["name"] for index in inspector.get_indexes("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for name, columns, predicate in _INDEXES:
        if name not in existing_indexes:
            op.create_index(
                name,
                "runtime_inbox",
                list(columns),
                schema=RUNTIME_SCHEMA,
                postgresql_where=sa.text(predicate),
            )


def downgrade() -> None:
    """删除 Revision A 新增索引与字段。"""
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {index["name"] for index in inspector.get_indexes("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for name, _columns, _predicate in reversed(_INDEXES):
        if name in existing_indexes:
            op.drop_index(name, table_name="runtime_inbox", schema=RUNTIME_SCHEMA)

    for column_name in reversed(_MILLISECOND_COLUMNS):
        op.alter_column(
            "runtime_inbox",
            column_name,
            schema=RUNTIME_SCHEMA,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )

    existing_columns = {column["name"] for column in inspector.get_columns("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for column in reversed(_COLUMNS):
        if column.name in existing_columns:
            op.drop_column("runtime_inbox", column.name, schema=RUNTIME_SCHEMA)
