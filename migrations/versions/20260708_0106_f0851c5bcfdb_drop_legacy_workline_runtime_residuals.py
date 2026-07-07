"""drop legacy workline runtime residuals

Revision ID: f0851c5bcfdb
Revises: de288342b42d
Create Date: 2026-07-08 01:06:07.871890+08:00

"""

# ruff: noqa: S608

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f0851c5bcfdb"
down_revision: Union[str, Sequence[str], None] = "de288342b42d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIZ_SCHEMA = "wes_biz"
RUNTIME_SCHEMA = "wes_runtime"
WORK_LINES_TABLE = "work_lines"
PROJECTION_TABLE = "workline_runtime_status_projections"
BIN_TRANSIT_TABLE = "bin_transit_memberships"
RUNTIME_STATUS_CONSTRAINT = "worklineruntimestatus"
WORKLINE_RUNTIME_VALUES = ("READY", "STOPPED", "STARTING", "ESTOPPED", "RECONCILING")
DOWNGRADE_WORKLINE_RUNTIME_VALUES = ("READY", "STOPPED", "RECONCILING", "ESTOPPED")
LEGACY_WORKLINE_RUNTIME_COLUMNS = (
    "runtime_status",
    "active_safety_incident_id",
    "stopped_at",
    "stopped_reason",
    "resumed_at",
)


def _set_destructive_statement_timeouts() -> None:
    """Keep destructive cleanup lock waits scoped to the migration transaction."""

    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("SET LOCAL statement_timeout = '60s'"))


def _drop_workline_runtime_status_constraint_if_exists() -> None:
    op.execute(
        sa.text(
            f'ALTER TABLE "{BIZ_SCHEMA}"."{WORK_LINES_TABLE}" DROP CONSTRAINT IF EXISTS "{RUNTIME_STATUS_CONSTRAINT}"'
        )
    )


def _drop_workline_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{BIZ_SCHEMA}"."{index_name}"'))


def _drop_workline_column_if_exists(column_name: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{BIZ_SCHEMA}"."{WORK_LINES_TABLE}" DROP COLUMN IF EXISTS "{column_name}"'))


def _workline_runtime_status_enum() -> sa.Enum:
    return sa.Enum(
        *DOWNGRADE_WORKLINE_RUNTIME_VALUES,
        name=RUNTIME_STATUS_CONSTRAINT,
        native_enum=False,
        create_constraint=True,
        length=50,
    )


def upgrade() -> None:
    """Upgrade schema."""

    _set_destructive_statement_timeouts()

    op.create_table(
        PROJECTION_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workline_id", sa.BigInteger(), nullable=False, comment="WorkLine configuration id"),
        sa.Column("runtime_status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("stopped_at", sa.DateTime(), nullable=True, comment="naive UTC for DB"),
        sa.Column("stopped_reason", sa.String(length=200), nullable=True),
        sa.Column("resumed_at", sa.DateTime(), nullable=True, comment="naive UTC for DB"),
        sa.Column("active_safety_incident_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "runtime_status IN ('READY', 'STOPPED', 'STARTING', 'ESTOPPED', 'RECONCILING')",
            name="ck_wrt_status_proj_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ux_wrt_status_proj_workline",
        PROJECTION_TABLE,
        ["workline_id"],
        unique=True,
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_wrt_status_proj_status",
        PROJECTION_TABLE,
        ["runtime_status"],
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_wrt_status_proj_safety_incident",
        PROJECTION_TABLE,
        ["active_safety_incident_id"],
        schema=RUNTIME_SCHEMA,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{RUNTIME_SCHEMA}"."{PROJECTION_TABLE}" (
                workline_id,
                runtime_status,
                source,
                stopped_at,
                stopped_reason,
                resumed_at,
                active_safety_incident_id,
                evidence_json
            )
            SELECT
                id,
                COALESCE(runtime_status, 'STOPPED'),
                'migration:f0851c5bcfdb',
                stopped_at,
                stopped_reason,
                resumed_at,
                active_safety_incident_id,
                jsonb_build_object(
                    'migrated_from', 'wes_biz.work_lines',
                    'migration_revision', 'f0851c5bcfdb'
                )
            FROM "{BIZ_SCHEMA}"."{WORK_LINES_TABLE}"
            WHERE is_deleted = false
            ON CONFLICT (workline_id) DO UPDATE SET
                runtime_status = EXCLUDED.runtime_status,
                source = EXCLUDED.source,
                stopped_at = EXCLUDED.stopped_at,
                stopped_reason = EXCLUDED.stopped_reason,
                resumed_at = EXCLUDED.resumed_at,
                active_safety_incident_id = EXCLUDED.active_safety_incident_id,
                evidence_json = EXCLUDED.evidence_json
            """
        )
    )

    for index_name in (
        "ix_wes_biz_work_lines_runtime_status",
        "ix_wes_biz_work_lines_active_safety_incident_id",
        "ix_wes_biz_work_lines_stopped_at",
        "ix_wes_biz_work_lines_resumed_at",
    ):
        _drop_workline_index_if_exists(index_name)
    _drop_workline_runtime_status_constraint_if_exists()
    for column_name in LEGACY_WORKLINE_RUNTIME_COLUMNS:
        _drop_workline_column_if_exists(column_name)

    op.execute(sa.text(f'DROP TABLE IF EXISTS "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" CASCADE'))


def downgrade() -> None:
    """Downgrade schema."""

    _set_destructive_statement_timeouts()

    # Data boundary: bin_transit_memberships rows are not recoverable from this
    # downgrade. Restore that table from a database snapshot if historical queue
    # membership rows are required after rollback.
    op.add_column(
        WORK_LINES_TABLE,
        sa.Column(
            "runtime_status",
            _workline_runtime_status_enum(),
            server_default="STOPPED",
            nullable=False,
        ),
        schema=BIZ_SCHEMA,
    )
    op.add_column(
        WORK_LINES_TABLE,
        sa.Column("active_safety_incident_id", sa.BigInteger(), nullable=True),
        schema=BIZ_SCHEMA,
    )
    op.add_column(WORK_LINES_TABLE, sa.Column("stopped_at", sa.DateTime(), nullable=True), schema=BIZ_SCHEMA)
    op.add_column(
        WORK_LINES_TABLE, sa.Column("stopped_reason", sa.String(length=200), nullable=True), schema=BIZ_SCHEMA
    )
    op.add_column(WORK_LINES_TABLE, sa.Column("resumed_at", sa.DateTime(), nullable=True), schema=BIZ_SCHEMA)

    op.execute(
        sa.text(
            f"""
            UPDATE "{BIZ_SCHEMA}"."{WORK_LINES_TABLE}" AS workline
               SET runtime_status = CASE
                       WHEN projection.runtime_status = 'STARTING' THEN 'STOPPED'
                       ELSE COALESCE(projection.runtime_status, 'STOPPED')
                   END,
                   active_safety_incident_id = projection.active_safety_incident_id,
                   stopped_at = projection.stopped_at,
                   stopped_reason = projection.stopped_reason,
                   resumed_at = projection.resumed_at
              FROM "{RUNTIME_SCHEMA}"."{PROJECTION_TABLE}" AS projection
             WHERE projection.workline_id = workline.id
            """
        )
    )
    op.create_index(
        "ix_wes_biz_work_lines_runtime_status",
        WORK_LINES_TABLE,
        ["runtime_status"],
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_work_lines_active_safety_incident_id",
        WORK_LINES_TABLE,
        ["active_safety_incident_id"],
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_work_lines_stopped_at",
        WORK_LINES_TABLE,
        ["stopped_at"],
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_work_lines_resumed_at",
        WORK_LINES_TABLE,
        ["resumed_at"],
        schema=BIZ_SCHEMA,
    )

    op.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" (
                id BIGSERIAL PRIMARY KEY,
                bin_code VARCHAR(100),
                placeholder_key VARCHAR(240),
                workline_id BIGINT,
                workline_code VARCHAR(50),
                current_queue VARCHAR(80) NOT NULL,
                membership_status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
                handling_operation_id BIGINT,
                handling_move_id BIGINT,
                trace_id VARCHAR(100),
                workline_session_id BIGINT,
                entered_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
                left_at TIMESTAMP WITHOUT TIME ZONE,
                evidence_json JSONB NOT NULL DEFAULT '{{}}'::JSONB,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
                updated_at TIMESTAMP WITHOUT TIME ZONE,
                CONSTRAINT bintransitqueue CHECK (
                    current_queue IN (
                        'INFEED_BUFFER_QUEUE',
                        'ENTRY_SCAN_QUEUE',
                        'WORKSTATION_WAIT_QUEUE',
                        'WORKSTATION_ACTIVE',
                        'EXIT_ROUTING_SCAN_QUEUE',
                        'RETURN_SCAN_QUEUE',
                        'RETURN_WAIT_QUEUE',
                        'NG_REJECT_QUEUE'
                    )
                ),
                CONSTRAINT bintransitmembershipstatus CHECK (
                    membership_status IN ('ACTIVE', 'LEFT', 'RECONCILING')
                ),
                CONSTRAINT fk_bin_transit_memberships_workline_id_work_lines
                    FOREIGN KEY (workline_id) REFERENCES "{BIZ_SCHEMA}"."{WORK_LINES_TABLE}"(id),
                CONSTRAINT fk_btm_workline_session
                    FOREIGN KEY (workline_session_id) REFERENCES "{BIZ_SCHEMA}"."workline_sessions"(id),
                CONSTRAINT fk_btm_handling_operation
                    FOREIGN KEY (handling_operation_id) REFERENCES "{BIZ_SCHEMA}"."handling_operations"(id),
                CONSTRAINT fk_btm_handling_move
                    FOREIGN KEY (handling_move_id) REFERENCES "{BIZ_SCHEMA}"."handling_operation_moves"(id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_bin_transit_memberships_active_bin
            ON "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" (bin_code)
            WHERE bin_code IS NOT NULL AND left_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_bin_transit_memberships_active_placeholder
            ON "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" (placeholder_key)
            WHERE placeholder_key IS NOT NULL AND left_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE INDEX IF NOT EXISTS ix_bin_transit_memberships_workline_queue
            ON "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" (workline_id, current_queue)
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE INDEX IF NOT EXISTS ix_bin_transit_memberships_session_entered
            ON "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" (workline_session_id, entered_at)
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE INDEX IF NOT EXISTS ix_bin_transit_memberships_trace_entered
            ON "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" (trace_id, entered_at)
            """
        )
    )

    op.drop_index(
        "ix_wrt_status_proj_safety_incident",
        table_name=PROJECTION_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    op.drop_index(
        "ix_wrt_status_proj_status",
        table_name=PROJECTION_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    op.drop_index(
        "ux_wrt_status_proj_workline",
        table_name=PROJECTION_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    op.drop_table(PROJECTION_TABLE, schema=RUNTIME_SCHEMA)
