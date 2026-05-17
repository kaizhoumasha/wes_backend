"""add resource fact projections

Revision ID: 7541d77ecf3b
Revises: 13140cee49a7
Create Date: 2026-05-16 18:39:20.333757+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7541d77ecf3b"
down_revision: Union[str, Sequence[str], None] = "13140cee49a7"
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


def _soft_delete_columns() -> list[sa.Column]:
    return [
        sa.Column("deleted_by", sa.BigInteger(), nullable=True, comment="删除人ID"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False, comment="是否已删除"),
    ]


def _json_object_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'{}'::json"),
        nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "resource_state_events",
        *_enterprise_columns(),
        sa.Column("event_code", sa.String(length=160), nullable=False, comment="资源事件唯一编码"),
        sa.Column(
            "event_type",
            sa.Enum(
                "RACK_ARRIVED",
                "RACK_DEPARTED",
                "BIN_MOUNTED",
                "BIN_UNMOUNTED",
                "MATERIAL_MOUNTED",
                "MATERIAL_UNMOUNTED",
                "EXCHANGE_STATUS_UPDATED",
                "RESOURCE_RECONCILED",
                name="resourcestateeventtype",
                native_enum=False,
                create_constraint=True,
                length=80,
            ),
            nullable=False,
            comment="资源事件类型",
        ),
        sa.Column(
            "resource_type",
            sa.Enum(
                "WORKLINE",
                "DEVICE",
                "RACK",
                "BIN",
                "MATERIAL",
                "LOCATION",
                "EXCHANGE_TASK",
                name="resourcetype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="资源类型",
        ),
        sa.Column("resource_code", sa.String(length=120), nullable=False, comment="资源编码"),
        sa.Column(
            "source_system",
            sa.Enum(
                "WMS",
                "RCS",
                "ECS",
                "WES_RUNTIME",
                "MANUAL_IMPORT",
                "MANUAL",
                name="resourcesourcesystem",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="来源系统",
        ),
        sa.Column("source_event_id", sa.String(length=200), nullable=False, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.Column("session_id", sa.String(length=100), nullable=True, comment="WorkLine Session"),
        _json_object_column("payload_json", comment="事件事实"),
        sa.Column("occurred_at", sa.DateTime(), nullable=False, comment="事实发生时间"),
        sa.Column("received_at", sa.DateTime(), nullable=False, comment="WES 接收时间"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_state_events_event_code",
        "resource_state_events",
        ["event_code"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_state_events_source_event",
        "resource_state_events",
        ["source_system", "source_event_id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_state_events_resource_time",
        "resource_state_events",
        ["resource_type", "resource_code", "occurred_at"],
        schema="wes_biz",
    )
    op.create_index("ix_resource_state_events_trace_id", "resource_state_events", ["trace_id"], schema="wes_biz")
    op.create_index("ix_resource_state_events_session_id", "resource_state_events", ["session_id"], schema="wes_biz")

    op.create_table(
        "resource_rack_placements",
        *_enterprise_columns(),
        *_soft_delete_columns(),
        sa.Column("rack_code", sa.String(length=80), nullable=False, comment="货架编码"),
        sa.Column("location_code", sa.String(length=80), nullable=False, comment="地码编码"),
        sa.Column(
            "placement_status",
            sa.Enum(
                "ARRIVED",
                "IN_TRANSIT",
                "DEPARTED",
                "UNKNOWN",
                name="rackplacementstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="位置投影状态",
        ),
        sa.Column(
            "source_system",
            sa.Enum(
                "WMS",
                "RCS",
                "ECS",
                "WES_RUNTIME",
                "MANUAL_IMPORT",
                "MANUAL",
                name="resourcesourcesystem",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="来源系统",
        ),
        sa.Column("source_task_id", sa.String(length=120), nullable=True, comment="WMS/RCS 搬运任务 ID"),
        sa.Column("source_event_id", sa.String(length=200), nullable=False, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.Column("session_id", sa.String(length=100), nullable=True, comment="WorkLine Session"),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="进入该关系的时间"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="离开该关系的时间"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_placements_active_rack",
        "resource_rack_placements",
        ["rack_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("ended_at IS NULL AND NOT is_deleted"),
    )
    op.create_index(
        "ix_resource_rack_placements_location_active",
        "resource_rack_placements",
        ["location_code", "ended_at"],
        schema="wes_biz",
    )

    op.create_table(
        "resource_rack_bin_mounts",
        *_enterprise_columns(),
        *_soft_delete_columns(),
        sa.Column("rack_code", sa.String(length=80), nullable=False, comment="货架编码"),
        sa.Column("rack_slot_code", sa.String(length=50), nullable=False, comment="货架槽位编码"),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="料箱编码"),
        sa.Column(
            "mount_status",
            sa.Enum(
                "MOUNTED",
                "UNMOUNTED",
                "EXCHANGING",
                "UNKNOWN",
                name="rackbinmountstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="料箱挂载状态",
        ),
        sa.Column(
            "source_system",
            sa.Enum(
                "ECS",
                "WMS_RCS",
                "WES_RUNTIME",
                "MANUAL_RECONCILIATION",
                name="resourcerelationsourcesystem",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="来源系统",
        ),
        sa.Column("source_event_id", sa.String(length=200), nullable=False, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.Column("session_id", sa.String(length=100), nullable=True, comment="WorkLine Session"),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="挂载确认时间"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="解除挂载时间"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_bin_mounts_active_slot",
        "resource_rack_bin_mounts",
        ["rack_code", "rack_slot_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("ended_at IS NULL AND NOT is_deleted"),
    )
    op.create_index(
        "ux_resource_rack_bin_mounts_active_bin",
        "resource_rack_bin_mounts",
        ["bin_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("ended_at IS NULL AND NOT is_deleted"),
    )

    op.create_table(
        "resource_rack_material_mounts",
        *_enterprise_columns(),
        *_soft_delete_columns(),
        sa.Column("rack_code", sa.String(length=80), nullable=False, comment="货架编码"),
        sa.Column("rack_slot_code", sa.String(length=50), nullable=False, comment="卡槽货位"),
        sa.Column("material_identity_key", sa.String(length=300), nullable=False, comment="WES 过程物料身份幂等键"),
        sa.Column("pkg_code", sa.String(length=200), nullable=True, comment="PKG 展示字段"),
        sa.Column("material_code", sa.String(length=120), nullable=True, comment="物料编码引用"),
        sa.Column("lot_code", sa.String(length=120), nullable=True, comment="批次展示字段"),
        sa.Column("vendor_code", sa.String(length=120), nullable=True, comment="供应商引用"),
        sa.Column("qty_snapshot", sa.Float(), nullable=True, comment="当时执行过程看到的数量"),
        sa.Column("wms_inventory_id", sa.String(length=120), nullable=True, comment="WMS 库存记录引用"),
        sa.Column("wms_inventory_version", sa.String(length=120), nullable=True, comment="WMS 库存或分拆版本引用"),
        sa.Column(
            "wms_split_policy",
            sa.Enum(
                "NOT_SPLITTABLE",
                "SPLITTABLE",
                "UNKNOWN",
                name="wmssplitpolicy",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="WMS 物料拆分策略",
        ),
        sa.Column(
            "wms_confirmation_status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "REJECTED",
                "NOT_REQUIRED",
                name="wmsconfirmationstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="WMS 确认状态",
        ),
        sa.Column("writeback_evidence_id", sa.BigInteger(), nullable=True, comment="关联 WMS 回写证据"),
        sa.Column(
            "mount_status",
            sa.Enum(
                "OCCUPIED",
                "REMOVED",
                "LOCKED",
                "UNKNOWN",
                name="rackmaterialmountstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="物料占用状态",
        ),
        sa.Column(
            "source_system",
            sa.Enum(
                "ECS",
                "WMS_RCS",
                "WES_RUNTIME",
                "MANUAL_RECONCILIATION",
                name="resourcerelationsourcesystem",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="来源系统",
        ),
        sa.Column("source_event_id", sa.String(length=200), nullable=False, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.Column("session_id", sa.String(length=100), nullable=True, comment="WorkLine Session"),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="占用确认时间"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="离开卡槽时间"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_material_mounts_active_slot",
        "resource_rack_material_mounts",
        ["rack_code", "rack_slot_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("ended_at IS NULL AND NOT is_deleted"),
    )
    op.create_index(
        "ix_resource_rack_material_mounts_identity_active",
        "resource_rack_material_mounts",
        ["material_identity_key", "ended_at"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("resource_rack_material_mounts", schema="wes_biz")
    op.drop_table("resource_rack_bin_mounts", schema="wes_biz")
    op.drop_table("resource_rack_placements", schema="wes_biz")
    op.drop_table("resource_state_events", schema="wes_biz")
