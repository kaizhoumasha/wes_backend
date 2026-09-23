"""清理过期设备观察与位置事件表

Revision ID: 398b9ace6c7b
Revises: 510f5006d385
Create Date: 2026-09-23 12:19:44.370605+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "398b9ace6c7b"
down_revision: Union[str, Sequence[str], None] = "510f5006d385"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """退役无生产写入的旧表；非空时拒绝丢弃既有事实。"""
    bind = op.get_bind()
    for table, lock_sql, exists_sql in (
        (
            "device_status_observations",
            "LOCK TABLE wes_biz.device_status_observations IN ACCESS EXCLUSIVE MODE",
            "SELECT EXISTS (SELECT 1 FROM wes_biz.device_status_observations)",
        ),
        (
            "runtime_location_events",
            "LOCK TABLE wes_biz.runtime_location_events IN ACCESS EXCLUSIVE MODE",
            "SELECT EXISTS (SELECT 1 FROM wes_biz.runtime_location_events)",
        ),
    ):
        op.execute(sa.text(lock_sql))
        if bind.scalar(sa.text(exists_sql)):
            raise RuntimeError(f"wes_biz.{table} contains historical facts; reconcile before retirement")
        op.drop_table(table, schema="wes_biz")


def downgrade() -> None:
    """恢复旧表结构；已清空的旧事实无法恢复。"""
    op.create_table(
        "device_status_observations",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
            comment="主键 ID",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("command_code", sa.String(length=100), nullable=True),
        sa.Column("contract_key", sa.String(length=100), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_command_code", sa.String(length=160), nullable=True),
        sa.Column("device_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_status_observations")),
        schema="wes_biz",
    )
    op.create_index(
        "ix_device_status_observations_device_received",
        "device_status_observations",
        ["device_code", "received_at", "id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_table(
        "runtime_location_events",
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
            comment="主键 ID",
        ),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("location_scope", sa.String(length=80), nullable=False),
        sa.Column("location_code", sa.String(length=300), nullable=False),
        sa.Column("business_step", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("source_event_id", sa.String(length=200), nullable=True),
        sa.Column("source_version", sa.String(length=80), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("external_reference_type", sa.String(length=100), nullable=True),
        sa.Column("external_reference_value", sa.String(length=300), nullable=True),
        sa.Column("provider_code", sa.String(length=80), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_location_events")),
        schema="wes_biz",
    )
    op.create_index(
        "ix_runtime_location_events_correlation_occurred",
        "runtime_location_events",
        ["correlation_id", "occurred_at"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ix_runtime_location_events_external_ref",
        "runtime_location_events",
        ["provider_code", "external_reference_type", "external_reference_value", "occurred_at"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ix_runtime_location_events_object_occurred",
        "runtime_location_events",
        ["object_type", "object_key", "occurred_at"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ix_runtime_location_events_source_event",
        "runtime_location_events",
        ["source", "source_event_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_business_step"),
        "runtime_location_events",
        ["business_step"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_correlation_id"),
        "runtime_location_events",
        ["correlation_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_external_reference_type"),
        "runtime_location_events",
        ["external_reference_type"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_external_reference_value"),
        "runtime_location_events",
        ["external_reference_value"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_location_code"),
        "runtime_location_events",
        ["location_code"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_location_scope"),
        "runtime_location_events",
        ["location_scope"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_object_key"),
        "runtime_location_events",
        ["object_key"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_object_type"),
        "runtime_location_events",
        ["object_type"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_occurred_at"),
        "runtime_location_events",
        ["occurred_at"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_provider_code"),
        "runtime_location_events",
        ["provider_code"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_source"),
        "runtime_location_events",
        ["source"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_source_event_id"),
        "runtime_location_events",
        ["source_event_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_location_events_source_version"),
        "runtime_location_events",
        ["source_version"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "uq_runtime_location_events_idempotency_key_not_null",
        "runtime_location_events",
        ["idempotency_key"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
