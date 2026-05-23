"""add bin cell occupancy aggregate

Revision ID: 286ddc5bc27d
Revises: bbaa8662d7fe
Create Date: 2026-05-19 14:17:24.452132+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "286ddc5bc27d"
down_revision: Union[str, Sequence[str], None] = "bbaa8662d7fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
_DROP_INDEX_SQL = {
    "ux_resource_bin_material_mounts_active_cell": (
        'DROP INDEX IF EXISTS "wes_biz"."ux_resource_bin_material_mounts_active_cell"'
    ),
    "ux_resource_bin_material_mounts_active_material_identity": (
        'DROP INDEX IF EXISTS "wes_biz"."ux_resource_bin_material_mounts_active_material_identity"'
    ),
}


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _drop_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(_DROP_INDEX_SQL[index_name]))


def _normalize_legacy_material_identity_keys() -> None:
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."resource_bin_material_mounts"
            SET material_identity_key =
                'MAT:' || split_part(material_identity_key, ':', 2) || '::'
                || split_part(material_identity_key, ':', 3) || ':'
                || split_part(material_identity_key, ':', 4)
            WHERE material_identity_key LIKE 'MAT:%'
              AND array_length(regexp_split_to_array(material_identity_key, ':'), 1) = 4
            """
        )
    )


def _guard_downgrade_stacked_active_mounts() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM "wes_biz"."resource_bin_material_mounts"
                    WHERE ended_at IS NULL
                    GROUP BY material_identity_key
                    HAVING COUNT(*) > 1
                ) OR EXISTS (
                    SELECT 1
                    FROM "wes_biz"."resource_bin_material_mounts"
                    WHERE ended_at IS NULL
                    GROUP BY bin_code, bin_cell_index
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade resource_bin_cell_occupancies while stacked active mounts exist; clear or end stacked mounts first';
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    _drop_index_if_exists("ux_resource_bin_material_mounts_active_cell")
    _drop_index_if_exists("ux_resource_bin_material_mounts_active_material_identity")
    _normalize_legacy_material_identity_keys()

    op.create_table(
        "resource_bin_cell_occupancies",
        *_data_columns(),
        sa.Column("bin_code", sa.String(length=80), nullable=False, comment="料箱编码"),
        sa.Column("bin_cell_code", sa.String(length=80), nullable=True, comment="料箱内部格位编码"),
        sa.Column("bin_cell_index", sa.String(length=20), nullable=False, comment="料箱内部格位序号"),
        sa.Column("material_identity_key", sa.String(length=300), nullable=False, comment="物料属性身份键"),
        sa.Column("material_code", sa.String(length=120), nullable=True, comment="物料编码引用"),
        sa.Column("lot_code", sa.String(length=120), nullable=True, comment="批次展示字段"),
        sa.Column("date_code", sa.String(length=80), nullable=True, comment="Date Code"),
        sa.Column("reel_count", sa.Integer(), nullable=False, server_default="0", comment="当前格位内 active 料盘数量"),
        sa.Column("used_depth_mm", sa.Float(), nullable=False, server_default="0", comment="当前格位已使用深度"),
        sa.Column("capacity_depth_mm", sa.Float(), nullable=True, comment="当前格位可用总深度"),
        sa.Column("remaining_depth_mm", sa.Float(), nullable=True, comment="当前格位剩余深度"),
        sa.Column(
            "occupancy_status",
            sa.Enum(
                "OCCUPIED",
                "FULL",
                "REMOVED",
                "UNKNOWN",
                name="bincelloccupancystatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="格位聚合占用状态",
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
        sa.Column("source_event_id", sa.String(length=200), nullable=False, comment="最近来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.Column("session_id", sa.String(length=100), nullable=True, comment="最近 WorkLine Session"),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="首次占用确认时间"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="格位占用结束时间"),
        sa.Column(
            "metadata_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
            comment="扩展属性",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.add_column(
        "resource_bin_material_mounts",
        sa.Column("bin_cell_occupancy_id", sa.BigInteger(), nullable=True, comment="关联料箱格位聚合占用 ID"),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            """
            WITH active_mounts AS (
                SELECT
                    id,
                    created_at,
                    updated_at,
                    bin_code,
                    bin_cell_code,
                    bin_cell_index,
                    material_identity_key,
                    material_code,
                    lot_code,
                    date_code,
                    CASE
                        WHEN reel_thickness ~ '^[[:space:]]*[0-9]+([.][0-9]+)?[[:space:]]*$'
                        THEN reel_thickness::double precision
                        ELSE NULL
                    END AS parsed_reel_thickness,
                    source_system,
                    source_event_id,
                    source_version,
                    trace_id,
                    session_id,
                    started_at
                FROM "wes_biz"."resource_bin_material_mounts"
                WHERE ended_at IS NULL
            ),
            grouped AS (
                SELECT
                    bin_code,
                    (array_agg(bin_cell_code ORDER BY started_at DESC, id DESC))[1] AS bin_cell_code,
                    bin_cell_index,
                    material_identity_key,
                    (array_agg(material_code ORDER BY started_at DESC, id DESC))[1] AS material_code,
                    (array_agg(lot_code ORDER BY started_at DESC, id DESC))[1] AS lot_code,
                    (array_agg(date_code ORDER BY started_at DESC, id DESC))[1] AS date_code,
                    COUNT(*)::integer AS reel_count,
                    SUM(COALESCE(parsed_reel_thickness, 0)) AS used_depth_mm,
                    (array_agg(source_system ORDER BY started_at DESC, id DESC))[1] AS source_system,
                    (array_agg(source_event_id ORDER BY started_at DESC, id DESC))[1] AS source_event_id,
                    (array_agg(source_version ORDER BY started_at DESC, id DESC))[1] AS source_version,
                    (array_agg(trace_id ORDER BY started_at DESC, id DESC))[1] AS trace_id,
                    (array_agg(session_id ORDER BY started_at DESC, id DESC))[1] AS session_id,
                    MIN(started_at) AS started_at,
                    MIN(created_at) AS created_at,
                    MAX(updated_at) AS updated_at
                FROM active_mounts
                GROUP BY bin_code, bin_cell_index, material_identity_key
            )
            INSERT INTO "wes_biz"."resource_bin_cell_occupancies" (
                created_at,
                updated_at,
                bin_code,
                bin_cell_code,
                bin_cell_index,
                material_identity_key,
                material_code,
                lot_code,
                date_code,
                reel_count,
                used_depth_mm,
                capacity_depth_mm,
                remaining_depth_mm,
                occupancy_status,
                source_system,
                source_event_id,
                source_version,
                trace_id,
                session_id,
                started_at,
                ended_at,
                metadata_json
            )
            SELECT
                COALESCE(created_at, CURRENT_TIMESTAMP),
                updated_at,
                bin_code,
                bin_cell_code,
                bin_cell_index,
                material_identity_key,
                material_code,
                lot_code,
                date_code,
                reel_count,
                used_depth_mm,
                NULL,
                NULL,
                'OCCUPIED',
                source_system,
                source_event_id,
                source_version,
                trace_id,
                session_id,
                COALESCE(started_at, CURRENT_TIMESTAMP),
                NULL,
                '{}'::json
            FROM grouped
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."resource_bin_material_mounts" AS mounts
            SET bin_cell_occupancy_id = occupancies.id
            FROM "wes_biz"."resource_bin_cell_occupancies" AS occupancies
            WHERE mounts.ended_at IS NULL AND mounts.bin_cell_occupancy_id IS NULL
              AND occupancies.ended_at IS NULL
              AND occupancies.bin_code = mounts.bin_code
              AND occupancies.bin_cell_index = mounts.bin_cell_index
              AND occupancies.material_identity_key = mounts.material_identity_key
            """
        )
    )
    op.create_index(
        "ix_wes_biz_resource_bin_material_mounts_bin_cell_occupancy_id",
        "resource_bin_material_mounts",
        ["bin_cell_occupancy_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_resource_bin_material_mounts_occupancy_active",
        "resource_bin_material_mounts",
        ["bin_cell_occupancy_id", "ended_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ux_resource_bin_cell_occupancies_active_cell",
        "resource_bin_cell_occupancies",
        ["bin_code", "bin_cell_index"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_resource_bin_cell_occupancies_identity_active",
        "resource_bin_cell_occupancies",
        ["material_identity_key", "ended_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_cell_occupancies_id",
        "resource_bin_cell_occupancies",
        ["id"],
        unique=True,
        schema=SCHEMA,
    )
    for column_name in (
        "bin_code",
        "bin_cell_code",
        "bin_cell_index",
        "material_identity_key",
        "material_code",
        "source_event_id",
        "trace_id",
        "session_id",
        "ended_at",
    ):
        op.create_index(
            f"ix_wes_biz_resource_bin_cell_occupancies_{column_name}",
            "resource_bin_cell_occupancies",
            [column_name],
            schema=SCHEMA,
        )


def downgrade() -> None:
    """Downgrade schema."""
    _guard_downgrade_stacked_active_mounts()

    op.drop_index(
        "ix_resource_bin_material_mounts_occupancy_active",
        table_name="resource_bin_material_mounts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_biz_resource_bin_material_mounts_bin_cell_occupancy_id",
        table_name="resource_bin_material_mounts",
        schema=SCHEMA,
    )
    op.drop_column("resource_bin_material_mounts", "bin_cell_occupancy_id", schema=SCHEMA)

    for column_name in (
        "ended_at",
        "session_id",
        "trace_id",
        "source_event_id",
        "material_code",
        "material_identity_key",
        "bin_cell_index",
        "bin_cell_code",
        "bin_code",
    ):
        op.drop_index(
            f"ix_wes_biz_resource_bin_cell_occupancies_{column_name}",
            table_name="resource_bin_cell_occupancies",
            schema=SCHEMA,
        )
    op.drop_index(
        "ix_wes_biz_resource_bin_cell_occupancies_id",
        table_name="resource_bin_cell_occupancies",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_resource_bin_cell_occupancies_identity_active",
        table_name="resource_bin_cell_occupancies",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_resource_bin_cell_occupancies_active_cell",
        table_name="resource_bin_cell_occupancies",
        schema=SCHEMA,
    )
    op.drop_table("resource_bin_cell_occupancies", schema=SCHEMA)

    op.create_index(
        "ux_resource_bin_material_mounts_active_material_identity",
        "resource_bin_material_mounts",
        ["material_identity_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ux_resource_bin_material_mounts_active_cell",
        "resource_bin_material_mounts",
        ["bin_code", "bin_cell_index"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
