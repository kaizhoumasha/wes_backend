"""add resource runtime base

Revision ID: 13140cee49a7
Revises: 78ff506d4d9a
Create Date: 2026-05-16 17:50:26.499383+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "13140cee49a7"
down_revision: Union[str, Sequence[str], None] = "78ff506d4d9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("deleted_by", sa.BigInteger(), nullable=True, comment="删除人ID"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False, comment="是否已删除"),
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
        "resource_execution_zones",
        *_base_columns(),
        sa.Column("zone_code", sa.String(length=50), nullable=False, comment="WES 区域编码"),
        sa.Column("zone_name", sa.String(length=100), nullable=False, comment="区域名称"),
        sa.Column(
            "zone_type",
            sa.Enum(
                "KITTING",
                "SMT_STORAGE",
                "FULL_BOX_EXCHANGE",
                "RETURN",
                "LINE_BUFFER",
                name="executionzonetype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="区域类型",
        ),
        sa.Column("wms_zone_code", sa.String(length=100), nullable=True, comment="WMS 区域引用"),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DISABLED",
                name="resourcemasterstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="区域状态",
        ),
        _json_array_column("allowed_rack_types", comment="允许进入的货架类型"),
        sa.Column("max_concurrent_tasks", sa.Integer(), nullable=True, comment="并发任务上限"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_execution_zones_code_deleted",
        "resource_execution_zones",
        ["zone_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "resource_execution_locations",
        *_base_columns(),
        sa.Column("location_code", sa.String(length=80), nullable=False, comment="WES 地码编码"),
        sa.Column("zone_code", sa.String(length=50), nullable=False, comment="所属区域编码"),
        sa.Column(
            "location_type",
            sa.Enum(
                "WORK_STATION",
                "BUFFER",
                "STORAGE",
                "EXCHANGE_SLOT",
                "QUEUE_SLOT",
                name="executionlocationtype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="地码类型",
        ),
        sa.Column("wms_location_code", sa.String(length=100), nullable=True, comment="WMS/RCS 地码引用"),
        sa.Column("rack_capacity", sa.Integer(), nullable=False, comment="可容纳货架数量"),
        _json_array_column("allowed_rack_types", comment="允许货架类型"),
        sa.Column(
            "status",
            sa.Enum(
                "AVAILABLE",
                "OCCUPIED",
                "LOCKED",
                "DISABLED",
                "UNKNOWN",
                name="executionlocationstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="地码状态",
        ),
        _json_object_column("coordinates_json", comment="RCS 坐标透传"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_execution_locations_code_deleted",
        "resource_execution_locations",
        ["location_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )
    op.create_index(
        "ix_resource_execution_locations_zone_code",
        "resource_execution_locations",
        ["zone_code"],
        schema="wes_biz",
    )

    op.create_table(
        "resource_rack_types",
        *_base_columns(),
        sa.Column("rack_type_code", sa.String(length=50), nullable=False, comment="货架类型编码"),
        sa.Column("rack_type_name", sa.String(length=100), nullable=False, comment="货架类型名称"),
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
            nullable=False,
            comment="货架物理结构类型",
        ),
        sa.Column("slot_count", sa.Integer(), nullable=False, comment="标准槽位数量"),
        sa.Column("has_side", sa.Boolean(), nullable=False, comment="是否区分 A/B 面"),
        sa.Column("description", sa.String(length=500), nullable=True, comment="说明"),
        sa.Column("active", sa.Boolean(), nullable=False, comment="是否启用"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_types_code_deleted",
        "resource_rack_types",
        ["rack_type_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "resource_rack_slot_templates",
        *_base_columns(),
        sa.Column("rack_type_code", sa.String(length=50), nullable=False, comment="所属货架类型编码"),
        sa.Column("slot_code", sa.String(length=50), nullable=False, comment="货架槽位编码"),
        sa.Column(
            "side",
            sa.Enum("A", "B", "NONE", name="rackslotside", native_enum=False, create_constraint=True, length=20),
            nullable=False,
            comment="槽位面",
        ),
        sa.Column("layer_no", sa.Integer(), nullable=False, comment="层号"),
        sa.Column("position_no", sa.Integer(), nullable=False, comment="同层序号"),
        sa.Column(
            "slot_kind",
            sa.Enum(
                "BIN_SLOT",
                "MATERIAL_SLOT",
                name="rackslotkind",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="槽位承载对象类型",
        ),
        _json_array_column("allowed_bin_types", comment="允许的料箱类型"),
        _json_array_column("allowed_material_carrier_types", comment="允许的物料承载形态"),
        sa.Column("active", sa.Boolean(), nullable=False, comment="是否启用"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_rack_slot_templates_type_slot_deleted",
        "resource_rack_slot_templates",
        ["rack_type_code", "slot_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "resource_racks",
        *_base_columns(),
        sa.Column("rack_code", sa.String(length=80), nullable=False, comment="WES 货架编码"),
        sa.Column("wms_rack_id", sa.String(length=100), nullable=True, comment="WMS 货架 ID"),
        sa.Column("rack_type_code", sa.String(length=50), nullable=False, comment="货架类型编码"),
        sa.Column(
            "status",
            sa.Enum(
                "AVAILABLE",
                "LOCKED",
                "IN_TRANSIT",
                "AT_WORKLINE",
                "IN_EXCHANGE",
                "EXCEPTION",
                "DISABLED",
                "UNKNOWN",
                name="rackstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="货架执行状态",
        ),
        sa.Column("current_location_code", sa.String(length=80), nullable=True, comment="最后确认地码"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, comment="最近一次现场确认时间"),
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
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_racks_code_deleted",
        "resource_racks",
        ["rack_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )
    op.create_index("ix_resource_racks_rack_type_code", "resource_racks", ["rack_type_code"], schema="wes_biz")
    op.create_index(
        "ix_resource_racks_current_location_code", "resource_racks", ["current_location_code"], schema="wes_biz"
    )

    op.create_table(
        "resource_bin_types",
        *_base_columns(),
        sa.Column("bin_type_code", sa.String(length=50), nullable=False, comment="料箱类型编码"),
        sa.Column("bin_type_name", sa.String(length=100), nullable=False, comment="料箱类型名称"),
        sa.Column("description", sa.String(length=500), nullable=True, comment="说明"),
        sa.Column("active", sa.Boolean(), nullable=False, comment="是否启用"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_bin_types_code_deleted",
        "resource_bin_types",
        ["bin_type_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "resource_bin_slot_templates",
        *_base_columns(),
        sa.Column("bin_type_code", sa.String(length=50), nullable=False, comment="所属料箱类型编码"),
        sa.Column("bin_slot_code", sa.String(length=50), nullable=False, comment="料箱内槽位编码"),
        sa.Column(
            "slot_size",
            sa.Enum(
                "7INCH",
                "13INCH",
                "15INCH",
                "LARGE",
                name="binslotsize",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
            comment="槽位尺寸",
        ),
        sa.Column("max_depth_mm", sa.Integer(), nullable=True, comment="最大深度"),
        sa.Column("max_weight_g", sa.Integer(), nullable=True, comment="最大重量"),
        sa.Column("active", sa.Boolean(), nullable=False, comment="是否启用"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_bin_slot_templates_type_slot_deleted",
        "resource_bin_slot_templates",
        ["bin_type_code", "bin_slot_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "resource_bins",
        *_base_columns(),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="WES 料箱编码"),
        sa.Column("wms_bin_id", sa.String(length=100), nullable=True, comment="WMS 料箱 ID"),
        sa.Column("bin_type_code", sa.String(length=50), nullable=False, comment="料箱类型编码"),
        sa.Column(
            "status",
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
            comment="料箱执行状态",
        ),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, comment="最近一次现场确认时间"),
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
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        _json_object_column("metadata_json", comment="扩展属性"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_bins_code_deleted",
        "resource_bins",
        ["bin_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("NOT is_deleted"),
    )
    op.create_index("ix_resource_bins_bin_type_code", "resource_bins", ["bin_type_code"], schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("resource_bins", schema="wes_biz")
    op.drop_table("resource_bin_slot_templates", schema="wes_biz")
    op.drop_table("resource_bin_types", schema="wes_biz")
    op.drop_table("resource_racks", schema="wes_biz")
    op.drop_table("resource_rack_slot_templates", schema="wes_biz")
    op.drop_table("resource_rack_types", schema="wes_biz")
    op.drop_table("resource_execution_locations", schema="wes_biz")
    op.drop_table("resource_execution_zones", schema="wes_biz")
