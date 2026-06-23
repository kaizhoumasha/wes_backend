"""resource c0 session fk integrity

Revision ID: 9b660037b4bb
Revises: 194dcb39daf4
Create Date: 2026-06-23 02:37:14.617645+08:00

"""

# ruff: noqa: S608
from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b660037b4bb"
down_revision: Union[str, Sequence[str], None] = "194dcb39daf4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
WORKLINE_SESSION_FK_TARGET = "wes_biz.workline_sessions.id"
BIN_CELL_OCCUPANCY_FK_TARGET = "wes_biz.resource_bin_cell_occupancies.id"

RESOURCE_SESSION_TABLES = (
    ("resource_state_events", "payload_json"),
    ("resource_rack_placements", None),
    ("resource_rack_bin_mounts", None),
    ("resource_bin_placements", "metadata_json"),
    ("resource_bin_material_mounts", None),
    ("resource_bin_cell_occupancies", "metadata_json"),
)
RESOURCE_SESSION_FK_NAMES = {
    "resource_state_events": "fk_rse_workline_session",
    "resource_rack_placements": "fk_rrp_workline_session",
    "resource_rack_bin_mounts": "fk_rrbm_workline_session",
    "resource_bin_placements": "fk_rbp_workline_session",
    "resource_bin_material_mounts": "fk_rbmm_workline_session",
    "resource_bin_cell_occupancies": "fk_rbco_workline_session",
}


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.resource_c0_session_cleanup_report (
            id BIGSERIAL PRIMARY KEY,
            table_name VARCHAR(120) NOT NULL,
            row_id BIGINT,
            legacy_session_id VARCHAR(100) NOT NULL,
            cleanup_reason VARCHAR(120) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL
        )
        """
    )
    for table_name, json_column in RESOURCE_SESSION_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table_name} ADD COLUMN IF NOT EXISTS workline_session_id BIGINT")
        op.execute(
            f"""
            UPDATE {SCHEMA}.{table_name}
               SET workline_session_id = session_id::BIGINT
             WHERE workline_session_id IS NULL
               AND session_id ~ '^[0-9]+$'
            """
        )
        if json_column is not None:
            op.execute(
                f"""
                UPDATE {SCHEMA}.{table_name}
                   SET {json_column} = (
                       COALESCE({json_column}, '{{}}'::json)::jsonb || jsonb_build_object('legacy_session_id', session_id)
                   )::json
                 WHERE session_id IS NOT NULL
                   AND session_id !~ '^[0-9]+$'
                """
            )
        else:
            op.execute(
                f"""
                INSERT INTO {SCHEMA}.resource_c0_session_cleanup_report (
                    table_name,
                    row_id,
                    legacy_session_id,
                    cleanup_reason
                )
                SELECT
                    '{table_name}',
                    id,
                    session_id,
                    'LEGACY_SESSION_ID_NOT_NUMERIC'
                  FROM {SCHEMA}.{table_name}
                 WHERE session_id IS NOT NULL
                   AND session_id !~ '^[0-9]+$'
                """
            )
        op.execute(
            f"""
            INSERT INTO {SCHEMA}.resource_c0_session_cleanup_report (
                table_name,
                row_id,
                legacy_session_id,
                cleanup_reason
            )
            SELECT
                '{table_name}',
                source_table.id,
                source_table.session_id,
                'LEGACY_SESSION_ID_NUMERIC_ORPHAN'
              FROM {SCHEMA}.{table_name} AS source_table
              LEFT JOIN {SCHEMA}.workline_sessions AS session_table
                ON session_table.id = source_table.workline_session_id
             WHERE source_table.workline_session_id IS NOT NULL
               AND session_table.id IS NULL
            """
        )
        op.execute(
            f"""
            UPDATE {SCHEMA}.{table_name} AS source_table
               SET workline_session_id = NULL
              FROM {SCHEMA}.resource_c0_session_cleanup_report AS report
             WHERE report.table_name = '{table_name}'
               AND report.row_id = source_table.id
               AND report.cleanup_reason = 'LEGACY_SESSION_ID_NUMERIC_ORPHAN'
            """
        )
        op.execute(f"ALTER TABLE {SCHEMA}.{table_name} DROP COLUMN IF EXISTS session_id")
        op.create_index(
            f"ix_{SCHEMA}_{table_name}_workline_session_id",
            table_name,
            ["workline_session_id"],
            schema=SCHEMA,
        )
        op.create_foreign_key(
            RESOURCE_SESSION_FK_NAMES[table_name],
            table_name,
            "workline_sessions",
            ["workline_session_id"],
            ["id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
        )

    op.execute(
        f"""
        INSERT INTO {SCHEMA}.resource_c0_session_cleanup_report (
            table_name,
            row_id,
            legacy_session_id,
            cleanup_reason
        )
        SELECT
            'resource_bin_material_mounts',
            mount.id,
            mount.bin_cell_occupancy_id::TEXT,
            'ORPHAN_BIN_CELL_OCCUPANCY'
          FROM {SCHEMA}.resource_bin_material_mounts AS mount
          LEFT JOIN {SCHEMA}.resource_bin_cell_occupancies AS occupancy
            ON occupancy.id = mount.bin_cell_occupancy_id
         WHERE mount.bin_cell_occupancy_id IS NOT NULL
           AND occupancy.id IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.resource_bin_material_mounts AS mount
           SET bin_cell_occupancy_id = NULL
          FROM {SCHEMA}.resource_c0_session_cleanup_report AS report
         WHERE report.table_name = 'resource_bin_material_mounts'
           AND report.row_id = mount.id
           AND report.cleanup_reason = 'ORPHAN_BIN_CELL_OCCUPANCY'
        """
    )
    op.create_foreign_key(
        "fk_resource_bin_material_mounts_bin_cell_occupancy_id",
        "resource_bin_material_mounts",
        "resource_bin_cell_occupancies",
        ["bin_cell_occupancy_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_resource_bin_material_mounts_bin_cell_occupancy_id",
        "resource_bin_material_mounts",
        schema=SCHEMA,
        type_="foreignkey",
    )

    for table_name, json_column in reversed(RESOURCE_SESSION_TABLES):
        op.drop_constraint(
            RESOURCE_SESSION_FK_NAMES[table_name],
            table_name,
            schema=SCHEMA,
            type_="foreignkey",
        )
        op.drop_index(f"ix_{SCHEMA}_{table_name}_workline_session_id", table_name=table_name, schema=SCHEMA)
        op.execute(f"ALTER TABLE {SCHEMA}.{table_name} ADD COLUMN IF NOT EXISTS session_id VARCHAR(100)")
        op.execute(
            f"""
            UPDATE {SCHEMA}.{table_name}
               SET session_id = workline_session_id::TEXT
             WHERE session_id IS NULL
               AND workline_session_id IS NOT NULL
            """
        )
        op.execute(
            f"""
            UPDATE {SCHEMA}.{table_name} AS source_table
               SET session_id = report.legacy_session_id
              FROM {SCHEMA}.resource_c0_session_cleanup_report AS report
             WHERE report.table_name = '{table_name}'
               AND report.row_id = source_table.id
               AND report.cleanup_reason IN (
                   'LEGACY_SESSION_ID_NOT_NUMERIC',
                   'LEGACY_SESSION_ID_NUMERIC_ORPHAN'
               )
            """
        )
        if json_column is not None:
            op.execute(
                f"""
                UPDATE {SCHEMA}.{table_name}
                   SET session_id = ({json_column}->'legacy_session_id') #>> '{{}}'
                 WHERE session_id IS NULL
                   AND {json_column} IS NOT NULL
                   AND ({json_column}->'legacy_session_id') IS NOT NULL
                """
            )
        op.execute(f"ALTER TABLE {SCHEMA}.{table_name} DROP COLUMN IF EXISTS workline_session_id")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.resource_c0_session_cleanup_report")
