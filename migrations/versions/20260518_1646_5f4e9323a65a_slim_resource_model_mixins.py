"""slim resource model mixins

Revision ID: 5f4e9323a65a
Revises: 2f424528ea71
Create Date: 2026-05-18 16:46:31.502663+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f4e9323a65a"
down_revision: Union[str, Sequence[str], None] = "2f424528ea71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"

SOFT_ENTERPRISE_TABLES = (
    "resource_execution_zones",
    "resource_execution_locations",
    "resource_rack_types",
    "resource_rack_slot_templates",
    "resource_racks",
    "resource_bin_types",
    "resource_bin_slot_templates",
    "resource_bins",
    "resource_rack_placements",
    "resource_rack_bin_mounts",
    "resource_rack_material_mounts",
)

ENTERPRISE_ONLY_TABLES = (
    "resource_rack_releases",
    "resource_full_box_exchange_tasks",
)

ENTERPRISE_COLUMNS = ("updated_by", "created_by", "version")
SOFT_DELETE_COLUMNS = ("deleted_by", "deleted_at", "is_deleted")

MASTER_INDEX_REPLACEMENTS = (
    (
        "resource_execution_zones",
        "ux_resource_execution_zones_code_deleted",
        "ux_resource_execution_zones_code",
        ("zone_code",),
    ),
    (
        "resource_execution_locations",
        "ux_resource_execution_locations_code_deleted",
        "ux_resource_execution_locations_code",
        ("location_code",),
    ),
    (
        "resource_rack_types",
        "ux_resource_rack_types_code_deleted",
        "ux_resource_rack_types_code",
        ("rack_type_code",),
    ),
    (
        "resource_rack_slot_templates",
        "ux_resource_rack_slot_templates_type_slot_deleted",
        "ux_resource_rack_slot_templates_type_slot",
        ("rack_type_code", "slot_code"),
    ),
    (
        "resource_racks",
        "ux_resource_racks_code_deleted",
        "ux_resource_racks_code",
        ("rack_code",),
    ),
    (
        "resource_bin_types",
        "ux_resource_bin_types_code_deleted",
        "ux_resource_bin_types_code",
        ("bin_type_code",),
    ),
    (
        "resource_bin_slot_templates",
        "ux_resource_bin_slot_templates_type_slot_deleted",
        "ux_resource_bin_slot_templates_type_slot",
        ("bin_type_code", "bin_slot_code"),
    ),
    (
        "resource_bins",
        "ux_resource_bins_code_deleted",
        "ux_resource_bins_code",
        ("bin_code",),
    ),
)

ACTIVE_INDEXES = (
    ("resource_rack_placements", "ux_resource_rack_placements_active_rack", ("rack_code",)),
    ("resource_rack_bin_mounts", "ux_resource_rack_bin_mounts_active_slot", ("rack_code", "rack_slot_code")),
    ("resource_rack_bin_mounts", "ux_resource_rack_bin_mounts_active_bin", ("bin_code",)),
    ("resource_rack_material_mounts", "ux_resource_rack_material_mounts_active_slot", ("rack_code", "rack_slot_code")),
)


def _add_enterprise_columns(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        schema=SCHEMA,
    )
    op.add_column(
        table_name,
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        schema=SCHEMA,
    )
    op.add_column(
        table_name,
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        schema=SCHEMA,
    )


def _add_soft_delete_columns(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("deleted_by", sa.BigInteger(), nullable=True, comment="删除人ID"),
        schema=SCHEMA,
    )
    op.add_column(
        table_name,
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        schema=SCHEMA,
    )
    op.add_column(
        table_name,
        sa.Column("is_deleted", sa.Boolean(), server_default="FALSE", nullable=False, comment="是否已删除"),
        schema=SCHEMA,
    )


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, old_index, new_index, columns in MASTER_INDEX_REPLACEMENTS:
        op.drop_index(old_index, table_name=table_name, schema=SCHEMA)
        op.create_index(new_index, table_name, list(columns), unique=True, schema=SCHEMA)

    for table_name, index_name, columns in ACTIVE_INDEXES:
        op.drop_index(index_name, table_name=table_name, schema=SCHEMA)
        op.create_index(
            index_name,
            table_name,
            list(columns),
            unique=True,
            schema=SCHEMA,
            postgresql_where=sa.text("ended_at IS NULL"),
        )

    for table_name in SOFT_ENTERPRISE_TABLES:
        for column_name in (*SOFT_DELETE_COLUMNS, *ENTERPRISE_COLUMNS):
            op.drop_column(table_name, column_name, schema=SCHEMA)

    for table_name in ENTERPRISE_ONLY_TABLES:
        for column_name in ENTERPRISE_COLUMNS:
            op.drop_column(table_name, column_name, schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in SOFT_ENTERPRISE_TABLES:
        _add_soft_delete_columns(table_name)
        _add_enterprise_columns(table_name)

    for table_name in ENTERPRISE_ONLY_TABLES:
        _add_enterprise_columns(table_name)

    for table_name, old_index, new_index, columns in MASTER_INDEX_REPLACEMENTS:
        op.drop_index(new_index, table_name=table_name, schema=SCHEMA)
        op.create_index(
            old_index,
            table_name,
            list(columns),
            unique=True,
            schema=SCHEMA,
            postgresql_where=sa.text("NOT is_deleted"),
        )

    for table_name, index_name, columns in ACTIVE_INDEXES:
        op.drop_index(index_name, table_name=table_name, schema=SCHEMA)
        op.create_index(
            index_name,
            table_name,
            list(columns),
            unique=True,
            schema=SCHEMA,
            postgresql_where=sa.text("ended_at IS NULL AND NOT is_deleted"),
        )
