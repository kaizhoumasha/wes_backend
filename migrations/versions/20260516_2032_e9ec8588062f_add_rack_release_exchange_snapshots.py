"""add rack release exchange snapshots

Revision ID: e9ec8588062f
Revises: 1fdeed75fd3a
Create Date: 2026-05-16 20:32:01.146752+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e9ec8588062f"
down_revision: Union[str, Sequence[str], None] = "1fdeed75fd3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enterprise_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _json_object_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'{}'::json"),
        nullable=False,
        comment=comment,
    )


def _json_array_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'[]'::json"),
        nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "resource_rack_releases",
        *_enterprise_columns(),
        sa.Column("rack_release_id", sa.String(length=160), nullable=False, comment="释放周期业务 ID"),
        sa.Column("single_layer_rack_code", sa.String(length=80), nullable=False, comment="单层货架编码"),
        sa.Column("source_classifier_line_code", sa.String(length=100), nullable=True, comment="粗分线编码"),
        sa.Column("source_task_batch_id", sa.String(length=160), nullable=True, comment="粗分整架任务或批次"),
        sa.Column("source_event_id", sa.String(length=200), nullable=True, comment="来源事件 ID"),
        sa.Column(
            "release_status",
            sa.Enum(
                "CANDIDATE",
                "INBOX_CREATED",
                "SESSION_STARTED",
                "EXCHANGE_REQUESTED",
                "COMPLETED",
                "BLOCKED",
                "RECONCILING",
                "CANCELLED",
                name="rackreleasestatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="释放周期状态",
        ),
        sa.Column("released_at", sa.DateTime(), nullable=False, comment="整架完成时间"),
        sa.Column("moved_out_at", sa.DateTime(), nullable=True, comment="离开粗分机时间"),
        sa.Column("inbox_id", sa.BigInteger(), nullable=True, comment="关联 WorklineInbox"),
        sa.Column("session_id", sa.BigInteger(), nullable=True, comment="关联 WorklineSession"),
        sa.Column(
            "release_cycle_seq", sa.Integer(), server_default="1", nullable=False, comment="同一货架连续释放周期序号"
        ),
        sa.Column("idempotency_key", sa.String(length=300), nullable=False, comment="释放周期幂等键"),
        sa.Column("snapshot_hash", sa.String(length=128), nullable=False, comment="4 箱快照摘要"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_releases_release_id",
        "resource_rack_releases",
        ["rack_release_id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_releases_idempotency",
        "resource_rack_releases",
        ["idempotency_key"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_rack_releases_rack_cycle",
        "resource_rack_releases",
        ["single_layer_rack_code", "release_cycle_seq"],
        schema="wes_biz",
    )
    op.create_index("ix_resource_rack_releases_session", "resource_rack_releases", ["session_id"], schema="wes_biz")

    op.create_table(
        "resource_rack_release_bin_snapshots",
        *_enterprise_columns(),
        sa.Column("rack_release_id", sa.String(length=160), nullable=False, comment="释放周期业务 ID"),
        sa.Column("slot_code", sa.String(length=50), nullable=False, comment="单层货架槽位"),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="料箱编码"),
        sa.Column("bin_type_code", sa.String(length=80), nullable=True, comment="快照时料箱类型"),
        sa.Column(
            "bin_execution_status",
            sa.Enum(
                "EMPTY_VERIFIED",
                "IN_USE",
                "LOCKED",
                "FULL_SNAPSHOT",
                "EXCEPTION",
                "DISABLED",
                "UNKNOWN",
                name="binstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="快照时料箱执行状态",
        ),
        sa.Column("usage_snapshot", sa.Float(), nullable=True, comment="过程计算使用率"),
        _json_object_column("material_summary_json", comment="物料摘要，不作为库存主账"),
        _json_object_column("wms_inventory_refs_json", comment="WMS 库存记录引用与版本"),
        sa.Column("snapshot_id", sa.String(length=160), nullable=True, comment="料箱内容快照 ID"),
        sa.Column("content_snapshot_hash", sa.String(length=128), nullable=True, comment="内容快照摘要"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_release_bin_snapshots_slot",
        "resource_rack_release_bin_snapshots",
        ["rack_release_id", "slot_code"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_rack_release_bin_snapshots_bin",
        "resource_rack_release_bin_snapshots",
        ["bin_code"],
        schema="wes_biz",
    )

    op.create_table(
        "resource_bin_content_snapshots",
        *_enterprise_columns(),
        sa.Column("snapshot_id", sa.String(length=160), nullable=False, comment="快照业务 ID"),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="料箱编码"),
        sa.Column("source_session_id", sa.BigInteger(), nullable=True, comment="产生快照的 WorklineSession"),
        sa.Column("source_event_id", sa.String(length=200), nullable=True, comment="来源事件或命令结果"),
        sa.Column("captured_at", sa.DateTime(), nullable=False, comment="快照时间"),
        sa.Column(
            "snapshot_status",
            sa.Enum(
                "COMPLETE",
                "PARTIAL",
                "UNKNOWN",
                name="bincontentsnapshotstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="快照完整性",
        ),
        sa.Column("snapshot_hash", sa.String(length=128), nullable=False, comment="快照头和明细稳定摘要"),
        sa.Column("wms_snapshot_version", sa.String(length=160), nullable=True, comment="WMS 查询版本或时间"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_bin_content_snapshots_snapshot_id",
        "resource_bin_content_snapshots",
        ["snapshot_id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_bin_content_snapshots_bin_time",
        "resource_bin_content_snapshots",
        ["bin_code", "captured_at"],
        schema="wes_biz",
    )

    op.create_table(
        "resource_bin_content_snapshot_items",
        *_enterprise_columns(),
        sa.Column("snapshot_id", sa.String(length=160), nullable=False, comment="所属快照业务 ID"),
        sa.Column("bin_slot_code", sa.String(length=50), nullable=True, comment="料箱内部槽位"),
        sa.Column("pkg_code", sa.String(length=200), nullable=True, comment="PKG 展示字段"),
        sa.Column("material_code", sa.String(length=120), nullable=True, comment="物料编码引用"),
        sa.Column("vendor_code", sa.String(length=120), nullable=True, comment="供应商引用"),
        sa.Column("lot_code", sa.String(length=120), nullable=True, comment="批次展示字段"),
        sa.Column("date_code", sa.String(length=80), nullable=True, comment="Date Code"),
        sa.Column("qty_snapshot", sa.Float(), nullable=True, comment="当时执行过程看到的数量"),
        sa.Column("thickness_mm", sa.Float(), nullable=True, comment="厚度"),
        _json_object_column("dims_json", comment="尺寸"),
        sa.Column("wms_inventory_id", sa.String(length=160), nullable=True, comment="WMS 库存记录引用"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_bin_content_snapshot_items_snapshot",
        "resource_bin_content_snapshot_items",
        ["snapshot_id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_bin_content_snapshot_items_pkg",
        "resource_bin_content_snapshot_items",
        ["pkg_code"],
        schema="wes_biz",
    )

    op.create_table(
        "resource_full_box_exchange_tasks",
        *_enterprise_columns(),
        sa.Column("exchange_request_code", sa.String(length=200), nullable=False, comment="满箱交换请求编码"),
        sa.Column("rack_release_id", sa.String(length=160), nullable=False, comment="来源释放周期"),
        sa.Column("session_id", sa.BigInteger(), nullable=True, comment="关联 WorklineSession"),
        sa.Column("outbox_id", sa.BigInteger(), nullable=True, comment="EXTERNAL_HTTP Outbox"),
        sa.Column("dispatch_key", sa.String(length=200), nullable=True, comment="Outbox 派发键"),
        sa.Column(
            "exchange_status",
            sa.Enum(
                "REQUESTED",
                "ACCEPTED",
                "QUEUED",
                "IN_PROGRESS",
                "PHYSICAL_COMPLETED",
                "RESOURCE_PROJECTED",
                "WMS_CONFIRMED",
                "BUSINESS_COMPLETED",
                "WMS_REJECTED",
                "REJECTED",
                "FAILED",
                "CANCELLED",
                "RECONCILING",
                name="fullboxexchangestatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="满箱交换状态",
        ),
        sa.Column("exchange_area_code", sa.String(length=100), nullable=True, comment="满箱交换区"),
        _json_array_column("requested_bins_json", comment="建议交换的料箱槽位"),
        sa.Column("wms_rcs_task_id", sa.String(length=160), nullable=True, comment="WMS/RCS 任务 ID"),
        sa.Column("wms_rcs_event_id", sa.String(length=200), nullable=True, comment="最近一次 WMS/RCS 事件"),
        sa.Column("queue_position", sa.Integer(), nullable=True, comment="排队位置"),
        sa.Column("eta_seconds", sa.Integer(), nullable=True, comment="预计等待或完成时间"),
        sa.Column("failure_code", sa.String(length=120), nullable=True, comment="失败或拒绝原因"),
        sa.Column("failure_message", sa.String(length=500), nullable=True, comment="失败或拒绝描述"),
        sa.Column("request_payload_hash", sa.String(length=128), nullable=True, comment="请求摘要"),
        sa.Column("last_callback_payload_hash", sa.String(length=128), nullable=True, comment="最近回调摘要"),
        sa.Column("writeback_evidence_id", sa.BigInteger(), nullable=True, comment="关联 WMS 回写证据"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_full_box_exchange_tasks_request",
        "resource_full_box_exchange_tasks",
        ["exchange_request_code"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_full_box_exchange_tasks_release",
        "resource_full_box_exchange_tasks",
        ["rack_release_id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_full_box_exchange_tasks_outbox",
        "resource_full_box_exchange_tasks",
        ["outbox_id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_full_box_exchange_tasks_status",
        "resource_full_box_exchange_tasks",
        ["exchange_status"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("resource_full_box_exchange_tasks", schema="wes_biz")
    op.drop_table("resource_bin_content_snapshot_items", schema="wes_biz")
    op.drop_table("resource_bin_content_snapshots", schema="wes_biz")
    op.drop_table("resource_rack_release_bin_snapshots", schema="wes_biz")
    op.drop_table("resource_rack_releases", schema="wes_biz")
