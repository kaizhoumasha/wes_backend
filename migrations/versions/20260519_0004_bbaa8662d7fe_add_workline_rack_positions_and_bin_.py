"""add workline rack positions and bin material mounts

Revision ID: bbaa8662d7fe
Revises: 5f4e9323a65a
Create Date: 2026-05-19 00:04:41.440324+08:00

Phase B 破坏性裁剪说明：
本迁移会删除旧 resource 镜像表并收敛主对象/投影模型。downgrade 仅恢复当前 schema 形态，
不恢复被删除表的数据；上线前需完成备份或确认旧表数据已迁出。

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "bbaa8662d7fe"
down_revision: Union[str, Sequence[str], None] = "5f4e9323a65a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
OBSOLETE_RESOURCE_TABLES = (
    "resource_full_box_exchange_tasks",
    "resource_rack_release_bin_snapshots",
    "resource_rack_releases",
    "resource_wms_writeback_evidence",
    "resource_rack_material_mounts",
    "resource_execution_locations",
    "resource_execution_zones",
)


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
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


def _resource_master_status_check() -> str:
    return "status IN ('ACTIVE', 'DISABLED')"


def _resource_source_system_check(column_name: str = "source_system") -> str:
    return f"{column_name} IN ('WMS', 'RCS', 'ECS', 'WES_RUNTIME', 'MANUAL_IMPORT', 'MANUAL')"


def _resource_type_check() -> str:
    return "resource_type IN ('RACK', 'BIN', 'MATERIAL')"


def _legacy_resource_type_check() -> str:
    return "resource_type IN ('WORKLINE', 'DEVICE', 'RACK', 'BIN', 'MATERIAL', 'LOCATION', 'EXCHANGE_TASK')"


def _legacy_rack_status_check() -> str:
    return (
        "status IN "
        "('AVAILABLE', 'LOCKED', 'IN_TRANSIT', 'AT_WORKLINE', 'IN_EXCHANGE', 'EXCEPTION', 'DISABLED', 'UNKNOWN')"
    )


def _legacy_bin_status_check() -> str:
    return "status IN ('EMPTY_VERIFIED', 'IN_USE', 'LOCKED', 'FULL_SNAPSHOT', 'EXCEPTION', 'DISABLED', 'UNKNOWN')"


def _legacy_relation_source_system_check() -> str:
    return "source_system IN ('ECS', 'WMS_RCS', 'WES_RUNTIME', 'MANUAL_RECONCILIATION')"


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    constraint_names = (constraint_name, f"ck_{table_name}_{constraint_name}")
    for name in dict.fromkeys(constraint_names):
        op.execute(sa.text(f'ALTER TABLE "{SCHEMA}"."{table_name}" DROP CONSTRAINT IF EXISTS "{name}"'))


def _drop_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{SCHEMA}"."{index_name}"'))


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workline_rack_positions",
        *_data_columns(),
        sa.Column("workline_id", sa.BigInteger(), nullable=False, comment="关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=False, comment="工作线编码"),
        sa.Column("position_code", sa.String(length=80), nullable=False, comment="停靠位编码"),
        sa.Column("position_name", sa.String(length=120), nullable=False, comment="停靠位名称"),
        sa.Column(
            "position_role",
            sa.Enum(
                "SOURCE_STORAGE",
                "OUTPUT_BUFFER",
                name="worklinerackpositionrole",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="停靠位角色",
        ),
        sa.Column(
            "allowed_rack_kind",
            sa.Enum(
                "SINGLE_LAYER",
                "FIVE_LAYER",
                "RETURN",
                "TRANSFER",
                "PRODUCTION",
                name="rackkind",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="允许货架类型",
        ),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="1", comment="容量；Phase A 固定为 1"),
        sa.Column("logic_location_code", sa.String(length=120), nullable=True, comment="WES 逻辑位置"),
        sa.Column("external_location_code", sa.String(length=120), nullable=True, comment="外部地码证据"),
        sa.Column("device_role", sa.String(length=100), nullable=True, comment="关联设备角色"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100", comment="候选优先级"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否启用"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.CheckConstraint("capacity = 1", name="ck_workline_rack_positions_capacity_one"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_workline_rack_positions_line_position",
        "workline_rack_positions",
        ["workline_code", "position_code"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_rack_positions_id",
        "workline_rack_positions",
        ["id"],
        unique=True,
        schema=SCHEMA,
    )
    for column_name in (
        "workline_id",
        "workline_code",
        "position_code",
        "logic_location_code",
        "external_location_code",
        "device_role",
        "enabled",
    ):
        op.create_index(
            f"ix_wes_biz_workline_rack_positions_{column_name}",
            "workline_rack_positions",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "workline_bin_cell_reservations",
        *_data_columns(),
        sa.Column("reservation_key", sa.String(length=240), nullable=False, comment="预占幂等键"),
        sa.Column("workline_id", sa.BigInteger(), nullable=False, comment="关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=False, comment="工作线编码"),
        sa.Column("session_id", sa.BigInteger(), nullable=False, comment="关联 Session.id"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="trace ID"),
        sa.Column("pkg_code", sa.String(length=200), nullable=False, comment="PKG 编码"),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="料箱编码"),
        sa.Column("bin_cell_code", sa.String(length=80), nullable=True, comment="料箱格位编码"),
        sa.Column("bin_cell_index", sa.String(length=20), nullable=False, comment="料箱格位序号"),
        sa.Column(
            "reservation_status",
            sa.Enum(
                "PLANNED",
                "CONSUMED",
                "RELEASED",
                "CANCELLED",
                name="bincellreservationstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="预占状态",
        ),
        sa.Column("source_event_id", sa.String(length=200), nullable=True, comment="来源命令或事件"),
        sa.Column("reserved_at", sa.DateTime(), nullable=False, comment="预占时间"),
        sa.Column("consumed_at", sa.DateTime(), nullable=True, comment="消耗时间"),
        sa.Column("released_at", sa.DateTime(), nullable=True, comment="释放时间"),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="预占过期时间"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.ForeignKeyConstraint(["session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_workline_bin_cell_reservations_key",
        "workline_bin_cell_reservations",
        ["reservation_key"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ux_workline_bin_cell_reservations_active_cell",
        "workline_bin_cell_reservations",
        ["bin_code", "bin_cell_index"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("reservation_status = 'PLANNED'"),
    )
    op.create_index(
        "ix_workline_bin_cell_reservations_session",
        "workline_bin_cell_reservations",
        ["session_id", "reservation_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_bin_cell_reservations_id",
        "workline_bin_cell_reservations",
        ["id"],
        unique=True,
        schema=SCHEMA,
    )
    for column_name in (
        "reservation_key",
        "workline_id",
        "workline_code",
        "session_id",
        "trace_id",
        "pkg_code",
        "bin_code",
        "bin_cell_code",
        "bin_cell_index",
        "source_event_id",
        "consumed_at",
        "released_at",
        "expires_at",
    ):
        op.create_index(
            f"ix_wes_biz_workline_bin_cell_reservations_{column_name}",
            "workline_bin_cell_reservations",
            [column_name],
            schema=SCHEMA,
        )

    op.add_column(
        "resource_state_events",
        sa.Column("idempotency_key", sa.String(length=240), nullable=True, comment="资源事实幂等键"),
        schema=SCHEMA,
    )
    op.add_column("resource_state_events", sa.Column("workline_id", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column(
        "resource_state_events", sa.Column("workline_code", sa.String(length=50), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "resource_state_events", sa.Column("position_code", sa.String(length=80), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "resource_state_events",
        sa.Column("logic_location_code", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "resource_state_events",
        sa.Column("external_location_code", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.drop_index("ux_resource_state_events_source_event", table_name="resource_state_events", schema=SCHEMA)
    op.create_index(
        "ux_resource_state_events_idempotency",
        "resource_state_events",
        ["idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_resource_state_events_source_event",
        "resource_state_events",
        ["source_system", "source_event_id"],
        schema=SCHEMA,
    )
    for column_name in (
        "idempotency_key",
        "workline_id",
        "workline_code",
        "position_code",
        "logic_location_code",
        "external_location_code",
    ):
        op.create_index(
            f"ix_wes_biz_resource_state_events_{column_name}",
            "resource_state_events",
            [column_name],
            schema=SCHEMA,
        )

    op.add_column(
        "resource_rack_placements",
        sa.Column(
            "rack_kind",
            sa.Enum(
                "SINGLE_LAYER",
                "FIVE_LAYER",
                "RETURN",
                "TRANSFER",
                "PRODUCTION",
                name="rackkind",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
            comment="货架类型",
        ),
        schema=SCHEMA,
    )
    op.add_column("resource_rack_placements", sa.Column("workline_id", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column(
        "resource_rack_placements", sa.Column("workline_code", sa.String(length=50), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "resource_rack_placements", sa.Column("position_code", sa.String(length=80), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "resource_rack_placements", sa.Column("position_role", sa.String(length=80), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "resource_rack_placements",
        sa.Column("logic_location_code", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "resource_rack_placements",
        sa.Column("external_location_code", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.alter_column(
        "resource_rack_placements", "location_code", existing_type=sa.String(length=80), nullable=True, schema=SCHEMA
    )
    op.create_index(
        "ux_resource_rack_placements_active_workline_position",
        "resource_rack_placements",
        ["workline_code", "position_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND workline_code IS NOT NULL AND position_code IS NOT NULL"),
    )
    for column_name in (
        "workline_id",
        "workline_code",
        "position_code",
        "position_role",
        "logic_location_code",
        "external_location_code",
    ):
        op.create_index(
            f"ix_wes_biz_resource_rack_placements_{column_name}",
            "resource_rack_placements",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "resource_bin_material_mounts",
        *_data_columns(),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="料箱编码"),
        sa.Column("bin_cell_code", sa.String(length=80), nullable=True, comment="料箱内部格位编码"),
        sa.Column("bin_cell_index", sa.String(length=20), nullable=False, comment="料箱内部格位序号"),
        sa.Column("material_identity_key", sa.String(length=300), nullable=False, comment="物料属性身份键"),
        sa.Column("pkg_code", sa.String(length=200), nullable=True, comment="PKG 展示字段"),
        sa.Column("material_code", sa.String(length=120), nullable=True, comment="物料编码引用"),
        sa.Column("lot_code", sa.String(length=120), nullable=True, comment="批次展示字段"),
        sa.Column("date_code", sa.String(length=80), nullable=True, comment="Date Code"),
        sa.Column("qty_snapshot", sa.Float(), nullable=True, comment="当时执行过程看到的数量"),
        sa.Column("reel_diameter", sa.String(length=80), nullable=True, comment="料盘直径"),
        sa.Column("reel_thickness", sa.String(length=80), nullable=True, comment="料盘厚度"),
        sa.Column("wms_inventory_id", sa.String(length=120), nullable=True, comment="WMS 库存记录引用"),
        sa.Column("wms_inventory_version", sa.String(length=120), nullable=True, comment="WMS 库存或分拆版本引用"),
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
                name="binmaterialmountstatus",
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
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="占用确认时间"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="离开料箱格位时间"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_resource_bin_material_mounts_active_cell",
        "resource_bin_material_mounts",
        ["bin_code", "bin_cell_index"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ux_resource_bin_material_mounts_active_pkg",
        "resource_bin_material_mounts",
        ["pkg_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND pkg_code IS NOT NULL"),
    )
    op.create_index(
        "ux_resource_bin_material_mounts_active_wms_inventory",
        "resource_bin_material_mounts",
        ["wms_inventory_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND wms_inventory_id IS NOT NULL"),
    )
    op.create_index(
        "ux_resource_bin_material_mounts_active_material_identity",
        "resource_bin_material_mounts",
        ["material_identity_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_resource_bin_material_mounts_identity_active",
        "resource_bin_material_mounts",
        ["material_identity_key", "ended_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_material_mounts_id",
        "resource_bin_material_mounts",
        ["id"],
        unique=True,
        schema=SCHEMA,
    )
    for column_name in (
        "bin_code",
        "bin_cell_code",
        "bin_cell_index",
        "material_identity_key",
        "pkg_code",
        "material_code",
        "wms_inventory_id",
        "source_event_id",
        "trace_id",
        "session_id",
        "ended_at",
    ):
        op.create_index(
            f"ix_wes_biz_resource_bin_material_mounts_{column_name}",
            "resource_bin_material_mounts",
            [column_name],
            schema=SCHEMA,
        )

    op.add_column(
        "resource_bin_content_snapshots",
        sa.Column("snapshot_reason", sa.String(length=80), nullable=True, comment="快照原因"),
        schema=SCHEMA,
    )
    op.add_column(
        "resource_bin_content_snapshots",
        sa.Column("snapshot_group_key", sa.String(length=160), nullable=True, comment="快照分组键"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_content_snapshots_snapshot_reason",
        "resource_bin_content_snapshots",
        ["snapshot_reason"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_content_snapshots_snapshot_group_key",
        "resource_bin_content_snapshots",
        ["snapshot_group_key"],
        schema=SCHEMA,
    )
    op.add_column(
        "resource_bin_content_snapshot_items",
        sa.Column("bin_cell_code", sa.String(length=80), nullable=True, comment="料箱内部格位编码"),
        schema=SCHEMA,
    )
    op.add_column(
        "resource_bin_content_snapshot_items",
        sa.Column("bin_cell_index", sa.String(length=20), nullable=True, comment="料箱内部格位序号"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_content_snapshot_items_bin_cell_code",
        "resource_bin_content_snapshot_items",
        ["bin_cell_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_content_snapshot_items_bin_cell_index",
        "resource_bin_content_snapshot_items",
        ["bin_cell_index"],
        schema=SCHEMA,
    )

    # Phase B: resource 域只保留 RACK / BIN / MATERIAL 主对象与 active 投影。
    for table_name in OBSOLETE_RESOURCE_TABLES:
        op.drop_table(table_name, schema=SCHEMA)

    _drop_constraint_if_exists("resource_state_events", "resourcetype")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_state_events "
            "SET resource_type = CASE "
            "WHEN resource_type = 'MATERIAL' THEN 'MATERIAL' "
            "WHEN resource_type = 'BIN' OR resource_type = 'EXCHANGE_TASK' THEN 'BIN' "
            "ELSE 'RACK' END"
        )
    )
    op.create_check_constraint(
        "ck_resource_state_events_resource_type",
        "resource_state_events",
        _resource_type_check(),
        schema=SCHEMA,
    )

    _drop_index_if_exists("ix_resource_racks_current_location_code")
    _drop_constraint_if_exists("resource_racks", "rackstatus")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_racks SET status = CASE WHEN status = 'DISABLED' THEN 'DISABLED' ELSE 'ACTIVE' END"
        )
    )
    op.create_check_constraint(
        "ck_resource_racks_status_master",
        "resource_racks",
        _resource_master_status_check(),
        schema=SCHEMA,
    )
    op.drop_column("resource_racks", "last_seen_at", schema=SCHEMA)
    op.drop_column("resource_racks", "current_location_code", schema=SCHEMA)

    _drop_constraint_if_exists("resource_bins", "binstatus")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_bins SET status = CASE WHEN status = 'DISABLED' THEN 'DISABLED' ELSE 'ACTIVE' END"
        )
    )
    op.create_check_constraint(
        "ck_resource_bins_status_master",
        "resource_bins",
        _resource_master_status_check(),
        schema=SCHEMA,
    )
    op.drop_column("resource_bins", "last_seen_at", schema=SCHEMA)

    _drop_constraint_if_exists("resource_rack_bin_mounts", "resourcerelationsourcesystem")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_rack_bin_mounts "
            "SET source_system = CASE "
            "WHEN source_system = 'WMS_RCS' THEN 'WMS' "
            "WHEN source_system = 'MANUAL_RECONCILIATION' THEN 'MANUAL' "
            "ELSE source_system END"
        )
    )
    op.create_check_constraint(
        "ck_resource_rack_bin_mounts_source_system",
        "resource_rack_bin_mounts",
        _resource_source_system_check(),
        schema=SCHEMA,
    )

    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_bin_content_snapshot_items "
            "SET bin_cell_code = COALESCE(bin_cell_code, bin_slot_code), "
            "bin_cell_index = COALESCE(bin_cell_index, bin_slot_code) "
            "WHERE bin_slot_code IS NOT NULL"
        )
    )
    op.drop_column("resource_bin_content_snapshot_items", "bin_slot_code", schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "resource_bin_content_snapshot_items",
        sa.Column("bin_slot_code", sa.String(length=50), nullable=True, comment="料箱内部槽位"),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_bin_content_snapshot_items "
            "SET bin_slot_code = COALESCE(bin_cell_code, bin_cell_index)"
        )
    )

    _drop_constraint_if_exists("resource_rack_bin_mounts", "ck_resource_rack_bin_mounts_source_system")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_rack_bin_mounts "
            "SET source_system = CASE "
            "WHEN source_system = 'WMS' THEN 'WMS_RCS' "
            "WHEN source_system = 'MANUAL' THEN 'MANUAL_RECONCILIATION' "
            "WHEN source_system = 'RCS' THEN 'WMS_RCS' "
            "ELSE source_system END"
        )
    )
    op.create_check_constraint(
        "resourcerelationsourcesystem",
        "resource_rack_bin_mounts",
        _legacy_relation_source_system_check(),
        schema=SCHEMA,
    )

    op.add_column(
        "resource_bins",
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, comment="最近一次现场确认时间"),
        schema=SCHEMA,
    )
    _drop_constraint_if_exists("resource_bins", "ck_resource_bins_status_master")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_bins SET status = CASE WHEN status = 'DISABLED' THEN 'DISABLED' ELSE 'IN_USE' END"
        )
    )
    op.create_check_constraint("binstatus", "resource_bins", _legacy_bin_status_check(), schema=SCHEMA)

    op.add_column(
        "resource_racks",
        sa.Column("current_location_code", sa.String(length=80), nullable=True, comment="最后确认地码"),
        schema=SCHEMA,
    )
    op.add_column(
        "resource_racks",
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, comment="最近一次现场确认时间"),
        schema=SCHEMA,
    )
    _drop_constraint_if_exists("resource_racks", "ck_resource_racks_status_master")
    op.execute(
        sa.text(
            "UPDATE wes_biz.resource_racks "
            "SET status = CASE WHEN status = 'DISABLED' THEN 'DISABLED' ELSE 'AVAILABLE' END"
        )
    )
    op.create_check_constraint("rackstatus", "resource_racks", _legacy_rack_status_check(), schema=SCHEMA)
    op.create_index(
        "ix_resource_racks_current_location_code",
        "resource_racks",
        ["current_location_code"],
        schema=SCHEMA,
    )

    _drop_constraint_if_exists("resource_state_events", "ck_resource_state_events_resource_type")
    op.create_check_constraint("resourcetype", "resource_state_events", _legacy_resource_type_check(), schema=SCHEMA)

    op.drop_index(
        "ix_wes_biz_resource_bin_content_snapshot_items_bin_cell_index",
        table_name="resource_bin_content_snapshot_items",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_biz_resource_bin_content_snapshot_items_bin_cell_code",
        table_name="resource_bin_content_snapshot_items",
        schema=SCHEMA,
    )
    op.drop_column("resource_bin_content_snapshot_items", "bin_cell_index", schema=SCHEMA)
    op.drop_column("resource_bin_content_snapshot_items", "bin_cell_code", schema=SCHEMA)
    op.drop_index(
        "ix_wes_biz_resource_bin_content_snapshots_snapshot_group_key",
        table_name="resource_bin_content_snapshots",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_biz_resource_bin_content_snapshots_snapshot_reason",
        table_name="resource_bin_content_snapshots",
        schema=SCHEMA,
    )
    op.drop_column("resource_bin_content_snapshots", "snapshot_group_key", schema=SCHEMA)
    op.drop_column("resource_bin_content_snapshots", "snapshot_reason", schema=SCHEMA)

    for column_name in (
        "ended_at",
        "session_id",
        "trace_id",
        "source_event_id",
        "wms_inventory_id",
        "material_code",
        "pkg_code",
        "material_identity_key",
        "bin_cell_index",
        "bin_cell_code",
        "bin_code",
    ):
        op.drop_index(
            f"ix_wes_biz_resource_bin_material_mounts_{column_name}",
            table_name="resource_bin_material_mounts",
            schema=SCHEMA,
        )
    op.drop_index(
        "ix_wes_biz_resource_bin_material_mounts_id", table_name="resource_bin_material_mounts", schema=SCHEMA
    )
    op.drop_index(
        "ix_resource_bin_material_mounts_identity_active", table_name="resource_bin_material_mounts", schema=SCHEMA
    )
    op.drop_index(
        "ux_resource_bin_material_mounts_active_wms_inventory", table_name="resource_bin_material_mounts", schema=SCHEMA
    )
    op.drop_index(
        "ux_resource_bin_material_mounts_active_material_identity",
        table_name="resource_bin_material_mounts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_resource_bin_material_mounts_active_pkg", table_name="resource_bin_material_mounts", schema=SCHEMA
    )
    op.drop_index(
        "ux_resource_bin_material_mounts_active_cell", table_name="resource_bin_material_mounts", schema=SCHEMA
    )
    op.drop_table("resource_bin_material_mounts", schema=SCHEMA)

    for column_name in (
        "external_location_code",
        "logic_location_code",
        "position_role",
        "position_code",
        "workline_code",
        "workline_id",
    ):
        op.drop_index(
            f"ix_wes_biz_resource_rack_placements_{column_name}",
            table_name="resource_rack_placements",
            schema=SCHEMA,
        )
    op.drop_index(
        "ux_resource_rack_placements_active_workline_position",
        table_name="resource_rack_placements",
        schema=SCHEMA,
    )
    op.alter_column(
        "resource_rack_placements", "location_code", existing_type=sa.String(length=80), nullable=False, schema=SCHEMA
    )
    for column_name in (
        "external_location_code",
        "logic_location_code",
        "position_role",
        "position_code",
        "workline_code",
        "workline_id",
        "rack_kind",
    ):
        op.drop_column("resource_rack_placements", column_name, schema=SCHEMA)

    for column_name in (
        "external_location_code",
        "logic_location_code",
        "position_code",
        "workline_code",
        "workline_id",
        "idempotency_key",
    ):
        op.drop_index(
            f"ix_wes_biz_resource_state_events_{column_name}",
            table_name="resource_state_events",
            schema=SCHEMA,
        )
    op.drop_index("ix_resource_state_events_source_event", table_name="resource_state_events", schema=SCHEMA)
    op.drop_index("ux_resource_state_events_idempotency", table_name="resource_state_events", schema=SCHEMA)
    op.create_index(
        "ux_resource_state_events_source_event",
        "resource_state_events",
        ["source_system", "source_event_id"],
        unique=True,
        schema=SCHEMA,
    )
    for column_name in (
        "external_location_code",
        "logic_location_code",
        "position_code",
        "workline_code",
        "workline_id",
        "idempotency_key",
    ):
        op.drop_column("resource_state_events", column_name, schema=SCHEMA)

    op.drop_index(
        "ix_workline_bin_cell_reservations_session", table_name="workline_bin_cell_reservations", schema=SCHEMA
    )
    for column_name in (
        "expires_at",
        "released_at",
        "consumed_at",
        "source_event_id",
        "bin_cell_index",
        "bin_cell_code",
        "bin_code",
        "pkg_code",
        "trace_id",
        "session_id",
        "workline_code",
        "workline_id",
        "reservation_key",
    ):
        op.drop_index(
            f"ix_wes_biz_workline_bin_cell_reservations_{column_name}",
            table_name="workline_bin_cell_reservations",
            schema=SCHEMA,
        )
    op.drop_index(
        "ix_wes_biz_workline_bin_cell_reservations_id",
        table_name="workline_bin_cell_reservations",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_workline_bin_cell_reservations_active_cell", table_name="workline_bin_cell_reservations", schema=SCHEMA
    )
    op.drop_index("ux_workline_bin_cell_reservations_key", table_name="workline_bin_cell_reservations", schema=SCHEMA)
    op.drop_table("workline_bin_cell_reservations", schema=SCHEMA)

    for column_name in (
        "enabled",
        "device_role",
        "external_location_code",
        "logic_location_code",
        "position_code",
        "workline_code",
        "workline_id",
    ):
        op.drop_index(
            f"ix_wes_biz_workline_rack_positions_{column_name}",
            table_name="workline_rack_positions",
            schema=SCHEMA,
        )
    op.drop_index("ix_wes_biz_workline_rack_positions_id", table_name="workline_rack_positions", schema=SCHEMA)
    op.drop_index("ux_workline_rack_positions_line_position", table_name="workline_rack_positions", schema=SCHEMA)
    op.drop_table("workline_rack_positions", schema=SCHEMA)
