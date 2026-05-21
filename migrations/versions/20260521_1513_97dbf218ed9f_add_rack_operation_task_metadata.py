"""add rack operation task metadata

Revision ID: 97dbf218ed9f
Revises: 083e85d1bf93
Create Date: 2026-05-21 15:13:15.977851+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "97dbf218ed9f"
down_revision: Union[str, Sequence[str], None] = "083e85d1bf93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def _json_object_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'{}'::json"),
        nullable=False,
        comment=comment,
    )


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{SCHEMA}"."{table_name}" DROP CONSTRAINT IF EXISTS "{constraint_name}"'))


def _drop_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{SCHEMA}"."{index_name}"'))


def upgrade() -> None:
    """Upgrade schema."""
    _drop_constraint_if_exists("workline_rack_positions", "worklinerackpositionrole")
    _drop_constraint_if_exists("workline_rack_positions", "ck_workline_rack_positions_capacity_one")
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."workline_rack_positions"
            SET position_role = CASE position_role
                WHEN 'SOURCE_STORAGE' THEN 'SMT_CLASSIFIER_SINGLE_RACK_WORK'
                WHEN 'OUTPUT_BUFFER' THEN 'SMT_RACK_EXCHANGE_AREA'
                ELSE position_role
            END
            WHERE position_role IN ('SOURCE_STORAGE', 'OUTPUT_BUFFER')
            """
        )
    )
    op.create_check_constraint(
        "worklinerackpositionrole",
        "workline_rack_positions",
        (
            "position_role IN ("
            "'SMT_CLASSIFIER_SINGLE_RACK_WORK', "
            "'SMT_RACK_EXCHANGE_AREA', "
            "'SMT_SORTER_QUEUE', "
            "'SMT_SORTER_STATION', "
            "'SMT_EMPTY_RACK_AREA'"
            ")"
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_workline_rack_positions_capacity_positive",
        "workline_rack_positions",
        "capacity > 0",
        schema=SCHEMA,
    )
    _drop_index_if_exists("ux_resource_rack_placements_active_workline_position")
    op.create_index(
        "ix_resource_rack_placements_workline_position_active",
        "resource_rack_placements",
        ["workline_code", "position_code", "ended_at"],
        unique=False,
        schema=SCHEMA,
    )

    _drop_constraint_if_exists("workline_rack_tasks", "worklineracktasktype")
    _drop_constraint_if_exists("workline_rack_tasks", "worklineracktaskstatus")
    op.add_column(
        "workline_rack_tasks",
        sa.Column("operation_key", sa.String(length=240), nullable=True, comment="货架操作幂等键"),
        schema=SCHEMA,
    )
    op.add_column(
        "workline_rack_tasks",
        sa.Column("operation_type", sa.String(length=100), nullable=True, comment="货架操作类型"),
        schema=SCHEMA,
    )
    op.add_column(
        "workline_rack_tasks",
        sa.Column("sequence_no", sa.Integer(), nullable=True, comment="同一货架操作下的任务序号"),
        schema=SCHEMA,
    )
    op.add_column(
        "workline_rack_tasks",
        sa.Column("rack_kind", sa.String(length=50), nullable=True, comment="货架类型"),
        schema=SCHEMA,
    )
    op.add_column(
        "workline_rack_tasks",
        sa.Column("source_position_code", sa.String(length=100), nullable=True, comment="来源位置编码"),
        schema=SCHEMA,
    )
    op.add_column(
        "workline_rack_tasks",
        sa.Column("target_position_code", sa.String(length=100), nullable=True, comment="目标位置编码"),
        schema=SCHEMA,
    )
    op.add_column(
        "workline_rack_tasks",
        sa.Column("target_position_role", sa.String(length=50), nullable=True, comment="目标位置角色"),
        schema=SCHEMA,
    )
    op.add_column("workline_rack_tasks", _json_object_column("actions_json", comment="调度动作 payload"), schema=SCHEMA)
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."workline_rack_tasks"
            SET
                operation_key = COALESCE(operation_key, task_key),
                operation_type = COALESCE(operation_type, 'LEGACY_RACK_TASK'),
                sequence_no = COALESCE(sequence_no, 1),
                target_position_code = COALESCE(target_position_code, position_code),
                task_type = CASE task_type
                    WHEN 'MOVE_TO_EMPTY_AREA' THEN 'MOVE_RACK'
                    WHEN 'FULL_BOX_EXCHANGE' THEN 'ALLOCATE_AND_MOVE_RACK'
                    WHEN 'RACK_SUPPLY' THEN 'ALLOCATE_AND_MOVE_RACK'
                    ELSE task_type
                END
            """
        )
    )
    op.alter_column("workline_rack_tasks", "operation_key", nullable=False, schema=SCHEMA)
    op.alter_column("workline_rack_tasks", "operation_type", nullable=False, schema=SCHEMA)
    op.alter_column("workline_rack_tasks", "sequence_no", nullable=False, schema=SCHEMA)
    _drop_index_if_exists("ix_wes_biz_workline_rack_tasks_position_code")
    op.drop_column("workline_rack_tasks", "position_code", schema=SCHEMA)
    op.create_check_constraint(
        "worklineracktasktype",
        "workline_rack_tasks",
        "task_type IN ('MOVE_RACK', 'ALLOCATE_AND_MOVE_RACK', 'TURN_RACK_SIDE')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "worklineracktaskstatus",
        "workline_rack_tasks",
        "task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'TIMEOUT', 'RECONCILING', 'CANCELLED')",
        schema=SCHEMA,
    )
    for column_name in (
        "operation_key",
        "operation_type",
        "sequence_no",
        "rack_kind",
        "source_position_code",
        "target_position_code",
        "target_position_role",
    ):
        op.create_index(
            f"ix_wes_biz_workline_rack_tasks_{column_name}",
            "workline_rack_tasks",
            [column_name],
            schema=SCHEMA,
        )
    _drop_index_if_exists("ix_workline_rack_tasks_session_status")
    _drop_index_if_exists("ix_workline_rack_tasks_rack_status")
    op.create_index(
        "ux_workline_rack_tasks_operation_sequence",
        "workline_rack_tasks",
        ["operation_key", "sequence_no"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workline_rack_tasks_operation_status",
        "workline_rack_tasks",
        ["operation_key", "task_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workline_rack_tasks_session_operation",
        "workline_rack_tasks",
        ["material_session_id", "operation_key"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workline_rack_tasks_target_status",
        "workline_rack_tasks",
        ["workline_code", "target_position_code", "task_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    _drop_index_if_exists("ix_resource_rack_placements_workline_position_active")
    op.create_index(
        "ux_resource_rack_placements_active_workline_position",
        "resource_rack_placements",
        ["workline_code", "position_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND workline_code IS NOT NULL AND position_code IS NOT NULL"),
    )
    _drop_constraint_if_exists("workline_rack_positions", "worklinerackpositionrole")
    _drop_constraint_if_exists("workline_rack_positions", "ck_workline_rack_positions_capacity_positive")
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."workline_rack_positions"
            SET position_role = CASE position_role
                WHEN 'SMT_RACK_EXCHANGE_AREA' THEN 'OUTPUT_BUFFER'
                WHEN 'SMT_EMPTY_RACK_AREA' THEN 'OUTPUT_BUFFER'
                ELSE 'SOURCE_STORAGE'
            END
            WHERE position_role NOT IN ('SOURCE_STORAGE', 'OUTPUT_BUFFER')
            """
        )
    )
    op.create_check_constraint(
        "worklinerackpositionrole",
        "workline_rack_positions",
        "position_role IN ('SOURCE_STORAGE', 'OUTPUT_BUFFER')",
        schema=SCHEMA,
    )
    op.execute(sa.text('UPDATE "wes_biz"."workline_rack_positions" SET capacity = 1 WHERE capacity <> 1'))
    op.create_check_constraint(
        "ck_workline_rack_positions_capacity_one",
        "workline_rack_positions",
        "capacity = 1",
        schema=SCHEMA,
    )

    _drop_index_if_exists("ux_workline_rack_tasks_operation_sequence")
    _drop_index_if_exists("ix_workline_rack_tasks_operation_status")
    _drop_index_if_exists("ix_workline_rack_tasks_session_operation")
    _drop_index_if_exists("ix_workline_rack_tasks_target_status")
    for column_name in (
        "operation_key",
        "operation_type",
        "sequence_no",
        "rack_kind",
        "source_position_code",
        "target_position_code",
        "target_position_role",
    ):
        _drop_index_if_exists(f"ix_wes_biz_workline_rack_tasks_{column_name}")

    _drop_constraint_if_exists("workline_rack_tasks", "worklineracktasktype")
    _drop_constraint_if_exists("workline_rack_tasks", "worklineracktaskstatus")
    op.add_column(
        "workline_rack_tasks",
        sa.Column("position_code", sa.String(length=100), nullable=True, comment="目标位置编码"),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."workline_rack_tasks"
            SET
                position_code = COALESCE(position_code, target_position_code),
                task_status = CASE task_status
                    WHEN 'TIMEOUT' THEN 'RECONCILING'
                    ELSE task_status
                END,
                task_type = CASE task_type
                    WHEN 'MOVE_RACK' THEN 'MOVE_TO_EMPTY_AREA'
                    ELSE 'RACK_SUPPLY'
                END
            """
        )
    )
    op.create_check_constraint(
        "worklineracktasktype",
        "workline_rack_tasks",
        "task_type IN ('RACK_SUPPLY', 'FULL_BOX_EXCHANGE', 'MOVE_TO_EMPTY_AREA')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "worklineracktaskstatus",
        "workline_rack_tasks",
        "task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'RECONCILING', 'CANCELLED')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_rack_tasks_position_code",
        "workline_rack_tasks",
        ["position_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workline_rack_tasks_session_status",
        "workline_rack_tasks",
        ["material_session_id", "task_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workline_rack_tasks_rack_status",
        "workline_rack_tasks",
        ["rack_code", "task_status"],
        schema=SCHEMA,
    )
    for column_name in (
        "actions_json",
        "target_position_role",
        "target_position_code",
        "source_position_code",
        "rack_kind",
        "sequence_no",
        "operation_type",
        "operation_key",
    ):
        op.drop_column("workline_rack_tasks", column_name, schema=SCHEMA)
