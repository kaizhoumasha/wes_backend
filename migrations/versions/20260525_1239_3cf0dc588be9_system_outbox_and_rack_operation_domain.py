"""system outbox and rack operation domain

Revision ID: 3cf0dc588be9
Revises: 745068e173c2
Create Date: 2026-05-25 12:39:16.083928+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3cf0dc588be9"
down_revision: Union[str, Sequence[str], None] = "745068e173c2"
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


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.table_constraints
            WHERE table_schema = :schema
              AND table_name = :table_name
              AND constraint_name = :constraint_name
            LIMIT 1
            """
        ).bindparams(schema=SCHEMA, table_name=table_name, constraint_name=constraint_name)
    ).scalar()
    if exists:
        op.drop_constraint(constraint_name, table_name, schema=SCHEMA)


def _drop_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{SCHEMA}"."{index_name}"'))


def _discard_legacy_workline_runtime_rows() -> None:
    """丢弃旧 Workline 运行态引用，避免半迁移旧 outbox/rack task 数据。"""

    op.execute(sa.text("DELETE FROM wes_biz.workline_rack_tasks"))
    op.execute(sa.text("DELETE FROM wes_biz.workline_dispatch_attempts"))
    op.execute(sa.text("UPDATE wes_biz.workline_diagnostics SET outbox_id = NULL WHERE outbox_id IS NOT NULL"))
    op.execute(sa.text("UPDATE wes_biz.runtime_holds SET source_outbox_id = NULL WHERE source_outbox_id IS NOT NULL"))
    op.execute(
        sa.text(
            """
            UPDATE wes_biz.workline_sessions
            SET reconciliation_source_outbox_id = NULL
            WHERE reconciliation_source_outbox_id IS NOT NULL
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema."""

    _discard_legacy_workline_runtime_rows()

    op.add_column(
        "handling_operations",
        sa.Column(
            "completion_policy",
            sa.Enum(
                "CALLBACK_TRUSTED",
                "RESOURCE_PROJECTION_REQUIRED",
                "CALLBACK_PLUS_RECONCILIATION",
                name="operationcompletionpolicy",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="CALLBACK_TRUSTED",
            comment="完成确认策略",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_wes_biz_handling_operations_completion_policy"),
        "handling_operations",
        ["completion_policy"],
        schema=SCHEMA,
    )

    _drop_index_if_exists("ix_system_outbox_operation_status")
    _drop_constraint_if_exists("system_outbox", "system_outbox_operation_id_fkey")
    op.add_column(
        "system_outbox",
        sa.Column("device_id", sa.BigInteger(), nullable=True, comment="可选关联 Device.id"),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_system_outbox_device_id",
        "system_outbox",
        "devices",
        ["device_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "operation_domain", sa.String(length=50), nullable=False, server_default="WORKLINE", comment="操作域"
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "system_outbox",
        sa.Column("operation_key", sa.String(length=240), nullable=True, comment="操作幂等键"),
        schema=SCHEMA,
    )
    op.add_column(
        "system_outbox",
        sa.Column("blocked_by_reconciliation_session_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "system_outbox", sa.Column("blocked_by_runtime_hold_id", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column("system_outbox", sa.Column("blocked_device_id", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("system_outbox", sa.Column("blocked_workline_id", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("system_outbox", sa.Column("blocked_reason", sa.String(length=100), nullable=True), schema=SCHEMA)
    op.drop_column("system_outbox", "operation_id", schema=SCHEMA)
    op.create_foreign_key(
        "fk_system_outbox_blocked_by_runtime_hold_id",
        "system_outbox",
        "runtime_holds",
        ["blocked_by_runtime_hold_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        use_alter=True,
    )
    for column_name in (
        "device_id",
        "operation_domain",
        "operation_key",
        "blocked_by_reconciliation_session_id",
        "blocked_by_runtime_hold_id",
        "blocked_device_id",
        "blocked_workline_id",
    ):
        op.create_index(op.f(f"ix_wes_biz_system_outbox_{column_name}"), "system_outbox", [column_name], schema=SCHEMA)
    op.create_index(
        "ix_system_outbox_status_retry_created",
        "system_outbox",
        ["status", "next_retry_at", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_system_outbox_domain_operation", "system_outbox", ["operation_domain", "operation_key"], schema=SCHEMA
    )
    op.create_index(
        "ix_system_outbox_context_status", "system_outbox", ["workline_id", "session_id", "status"], schema=SCHEMA
    )
    op.create_index(
        "ix_system_outbox_blocked_release",
        "system_outbox",
        ["blocked_reason", "blocked_device_id", "blocked_workline_id"],
        schema=SCHEMA,
    )
    op.create_index("ix_system_outbox_retention", "system_outbox", ["status", "finished_at"], schema=SCHEMA)
    op.create_index(
        "ix_system_outbox_device_fifo",
        "system_outbox",
        ["dispatch_type", "device_id", "target_code", "status", "created_at"],
        postgresql_where=sa.text("dispatch_type = 'DEVICE_COMMAND'"),
        schema=SCHEMA,
    )

    op.create_table(
        "rack_operations",
        *_data_columns(),
        sa.Column("operation_key", sa.String(length=240), nullable=False, comment="货架操作幂等键"),
        sa.Column("operation_type", sa.String(length=100), nullable=False, comment="货架操作类型"),
        sa.Column(
            "operation_status",
            sa.Enum(
                "REQUESTED",
                "PENDING",
                "SUCCEEDED",
                "FAILED",
                "RECONCILING",
                "CANCELLED",
                name="rackoperationstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="REQUESTED",
            comment="货架操作状态",
        ),
        sa.Column(
            "completion_policy",
            sa.Enum(
                "CALLBACK_TRUSTED",
                "RESOURCE_PROJECTION_REQUIRED",
                "CALLBACK_PLUS_RECONCILIATION",
                name="operationcompletionpolicy",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="CALLBACK_PLUS_RECONCILIATION",
            comment="完成确认策略",
        ),
        sa.Column("workline_id", sa.BigInteger(), nullable=True, comment="可选关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=True, comment="可选工作线编码"),
        sa.Column("material_session_id", sa.BigInteger(), nullable=True, comment="可选关联物料/料盘 Session.id"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="Trace ID"),
        _json_object_column("request_json", comment="请求证据"),
        _json_object_column("result_json", comment="结果证据"),
        sa.Column("error_code", sa.String(length=100), nullable=True, comment="错误码"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误消息"),
        sa.Column("requested_at", sa.DateTime(), nullable=True, comment="请求时间"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.ForeignKeyConstraint(["material_session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ux_rack_operations_key", "rack_operations", ["operation_key"], unique=True, schema=SCHEMA)
    op.create_index(
        "ix_rack_operations_status_requested", "rack_operations", ["operation_status", "requested_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_rack_operations_context",
        "rack_operations",
        ["workline_id", "material_session_id", "operation_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "rack_tasks",
        *_data_columns(),
        sa.Column("task_key", sa.String(length=240), nullable=False, comment="任务幂等键"),
        sa.Column("operation_id", sa.BigInteger(), nullable=True, comment="关联 RackOperation.id"),
        sa.Column("operation_key", sa.String(length=240), nullable=False, comment="货架操作幂等键"),
        sa.Column("operation_type", sa.String(length=100), nullable=False, comment="货架操作类型"),
        sa.Column("sequence_no", sa.Integer(), nullable=False, comment="同一货架操作下的任务序号"),
        sa.Column(
            "task_type",
            sa.Enum(
                "MOVE_RACK",
                "ALLOCATE_AND_MOVE_RACK",
                "TURN_RACK_SIDE",
                name="racktasktype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="任务类型",
        ),
        sa.Column(
            "task_status",
            sa.Enum(
                "PLANNED",
                "REQUESTED",
                "IN_PROGRESS",
                "SUCCEEDED",
                "FAILED",
                "TIMEOUT",
                "RECONCILING",
                "CANCELLED",
                name="racktaskstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="PLANNED",
            comment="任务状态",
        ),
        sa.Column("workline_id", sa.BigInteger(), nullable=True, comment="可选关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=True, comment="可选工作线编码"),
        sa.Column("material_session_id", sa.BigInteger(), nullable=True, comment="可选关联物料/料盘 Session.id"),
        sa.Column("rack_kind", sa.String(length=50), nullable=True, comment="货架类型"),
        sa.Column("rack_code", sa.String(length=100), nullable=True, comment="货架编码"),
        sa.Column("source_position_code", sa.String(length=100), nullable=True, comment="来源位置编码"),
        sa.Column("target_position_code", sa.String(length=100), nullable=True, comment="目标位置编码"),
        sa.Column("target_position_role", sa.String(length=50), nullable=True, comment="目标位置角色"),
        sa.Column("dispatch_key", sa.String(length=240), nullable=True, comment="外部派发幂等键"),
        sa.Column("outbox_id", sa.BigInteger(), nullable=True, comment="关联 SystemOutbox.id"),
        sa.Column("target_code", sa.String(length=200), nullable=True, comment="外部目标逻辑编码"),
        sa.Column("source_system", sa.String(length=100), nullable=True, comment="外部系统"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="Trace ID"),
        sa.Column("source_event_id", sa.String(length=200), nullable=True, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        _json_object_column("request_json", comment="请求证据"),
        _json_object_column("actions_json", comment="调度动作 payload"),
        _json_object_column("callback_json", comment="回调证据"),
        _json_object_column("result_json", comment="结果证据"),
        sa.Column("error_code", sa.String(length=100), nullable=True, comment="错误码"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误消息"),
        sa.Column("requested_at", sa.DateTime(), nullable=True, comment="请求时间"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.ForeignKeyConstraint(["operation_id"], [f"{SCHEMA}.rack_operations.id"]),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.ForeignKeyConstraint(["material_session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.ForeignKeyConstraint(["outbox_id"], [f"{SCHEMA}.system_outbox.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ux_rack_tasks_key", "rack_tasks", ["task_key"], unique=True, schema=SCHEMA)
    op.create_index("ux_rack_tasks_dispatch_key", "rack_tasks", ["dispatch_key"], unique=True, schema=SCHEMA)
    op.create_index(
        "ux_rack_tasks_operation_sequence", "rack_tasks", ["operation_key", "sequence_no"], unique=True, schema=SCHEMA
    )
    op.create_index("ix_rack_tasks_operation_id_status", "rack_tasks", ["operation_id", "task_status"], schema=SCHEMA)
    op.create_index(
        "ux_rack_tasks_move_source_claim",
        "rack_tasks",
        ["workline_code", "source_position_code", "rack_code"],
        unique=True,
        postgresql_where=sa.text(
            "task_type = 'MOVE_RACK' "
            "AND task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING') "
            "AND workline_code IS NOT NULL "
            "AND source_position_code IS NOT NULL "
            "AND rack_code IS NOT NULL"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_rack_tasks_operation_status", "rack_tasks", ["operation_key", "task_status"], schema=SCHEMA)
    op.create_index(
        "ix_rack_tasks_session_operation", "rack_tasks", ["material_session_id", "operation_key"], schema=SCHEMA
    )
    op.create_index(
        "ix_rack_tasks_target_status",
        "rack_tasks",
        ["workline_code", "target_position_code", "task_status"],
        schema=SCHEMA,
    )

    for table_name, column_name in (
        ("workline_diagnostics", "outbox_id"),
        ("workline_dispatch_attempts", "outbox_id"),
        ("runtime_holds", "source_outbox_id"),
    ):
        _drop_constraint_if_exists(table_name, f"{table_name}_{column_name}_fkey")
        op.create_foreign_key(
            f"fk_{table_name}_{column_name}_system_outbox",
            table_name,
            "system_outbox",
            [column_name],
            ["id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
        )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_table("rack_tasks", schema=SCHEMA)
    op.drop_table("rack_operations", schema=SCHEMA)
    for index_name in (
        "ix_system_outbox_device_fifo",
        "ix_system_outbox_retention",
        "ix_system_outbox_blocked_release",
        "ix_system_outbox_context_status",
        "ix_system_outbox_domain_operation",
        "ix_system_outbox_status_retry_created",
    ):
        _drop_index_if_exists(index_name)
    _drop_constraint_if_exists("system_outbox", "fk_system_outbox_blocked_by_runtime_hold_id")
    _drop_constraint_if_exists("system_outbox", "fk_system_outbox_device_id")
    for column_name in (
        "blocked_reason",
        "blocked_workline_id",
        "blocked_device_id",
        "blocked_by_runtime_hold_id",
        "blocked_by_reconciliation_session_id",
        "operation_key",
        "operation_domain",
        "device_id",
    ):
        op.drop_column("system_outbox", column_name, schema=SCHEMA)
    op.drop_index(
        op.f("ix_wes_biz_handling_operations_completion_policy"), table_name="handling_operations", schema=SCHEMA
    )
    op.drop_column("handling_operations", "completion_policy", schema=SCHEMA)
