"""add handling core

Revision ID: 745068e173c2
Revises: c0ff648f8718
Create Date: 2026-05-22 14:49:45.907984+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "745068e173c2"
down_revision: Union[str, Sequence[str], None] = "c0ff648f8718"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


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


def _json_array_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'[]'::json"),
        nullable=False,
        comment=comment,
    )


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            ALTER TABLE {SCHEMA}.{table_name}
            DROP CONSTRAINT IF EXISTS {constraint_name}
            """
        )
    )


def _resource_state_event_type_check(include_bin_placement_events: bool) -> str:
    values = [
        "RACK_ARRIVED",
        "RACK_DEPARTED",
        "BIN_MOUNTED",
        "BIN_UNMOUNTED",
        "MATERIAL_MOUNTED",
        "MATERIAL_UNMOUNTED",
        "EXCHANGE_STATUS_UPDATED",
        "RESOURCE_RECONCILED",
    ]
    if include_bin_placement_events:
        values[2:2] = ["BIN_ARRIVED", "BIN_DEPARTED"]
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"event_type IN ({quoted})"


HANDLING_OBJECT_TYPE = sa.Enum(
    "RACK",
    "BIN",
    "COMPOSITE",
    name="handlingobjecttype",
    native_enum=False,
    create_constraint=True,
    length=50,
)
HANDLING_OPERATION_STATUS = sa.Enum(
    "PLANNED",
    "REQUESTED",
    "IN_PROGRESS",
    "SUCCEEDED",
    "FAILED",
    "TIMEOUT",
    "RECONCILING",
    "CANCELLED",
    name="handlingoperationstatus",
    native_enum=False,
    create_constraint=True,
    length=50,
)
HANDLING_MOVE_STATUS = sa.Enum(
    "PLANNED",
    "REQUESTED",
    "IN_PROGRESS",
    "SUCCEEDED",
    "FAILED",
    "TIMEOUT",
    "RECONCILING",
    "CANCELLED",
    name="handlingmovestatus",
    native_enum=False,
    create_constraint=True,
    length=50,
)
HANDLING_STEP_KIND = sa.Enum(
    "RESOURCE_RESERVATION",
    "EXTERNAL_REQUEST",
    "DEVICE_COMMAND",
    "RESOURCE_PROJECTION",
    "SNAPSHOT_CONFIRMATION",
    "SESSION_WAKEUP",
    name="handlingstepkind",
    native_enum=False,
    create_constraint=True,
    length=50,
)
HANDLING_STEP_STATUS = sa.Enum(
    "PLANNED",
    "READY",
    "REQUESTED",
    "IN_PROGRESS",
    "SUCCEEDED",
    "FAILED",
    "TIMEOUT",
    "RECONCILING",
    "CANCELLED",
    name="handlingstepstatus",
    native_enum=False,
    create_constraint=True,
    length=50,
)
SYSTEM_OUTBOX_STATUS = sa.Enum(
    "NEW",
    "DISPATCHING",
    "SENT",
    "BLOCKED_RESOURCE",
    "FAILED",
    "CANCELLED",
    name="systemoutboxstatus",
    native_enum=False,
    create_constraint=True,
    length=50,
)
SYSTEM_OUTBOX_DISPATCH_TYPE = sa.Enum(
    "DEVICE_COMMAND",
    "EXTERNAL_HTTP",
    "INTERNAL_SIGNAL",
    name="systemoutboxdispatchtype",
    native_enum=False,
    create_constraint=True,
    length=50,
)
SYSTEM_OUTBOX_TARGET_TYPE = sa.Enum(
    "DEVICE",
    "HTTP_ENDPOINT",
    "INTERNAL_SERVICE",
    name="systemoutboxtargettype",
    native_enum=False,
    create_constraint=True,
    length=50,
)


def upgrade() -> None:
    """Upgrade schema."""
    _drop_constraint_if_exists("resource_state_events", "resourcestateeventtype")
    op.create_check_constraint(
        "resourcestateeventtype",
        "resource_state_events",
        _resource_state_event_type_check(include_bin_placement_events=True),
        schema=SCHEMA,
    )

    op.create_table(
        "resource_bin_placements",
        *_data_columns(),
        sa.Column("bin_code", sa.String(length=80), nullable=True, comment="料箱编码"),
        sa.Column("placeholder_key", sa.String(length=120), nullable=True, comment="未扫码占位键"),
        sa.Column("position_type", sa.String(length=80), nullable=False, comment="位置类型"),
        sa.Column("position_code", sa.String(length=120), nullable=False, comment="位置编码"),
        sa.Column("workline_id", sa.BigInteger(), nullable=True, comment="关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=True, comment="工作线编码"),
        sa.Column(
            "placement_status",
            sa.Enum(
                "ARRIVED",
                "IN_TRANSIT",
                "DEPARTED",
                "UNKNOWN",
                name="binplacementstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="UNKNOWN",
            comment="料箱位置投影状态",
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
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="进入该位置的时间"),
        sa.Column("ended_at", sa.DateTime(), nullable=True, comment="离开该位置的时间"),
        _json_object_column("metadata_json", comment="扩展证据"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_resource_bin_placements_active_bin",
        "resource_bin_placements",
        ["bin_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("bin_code IS NOT NULL AND ended_at IS NULL"),
    )
    op.create_index(
        "ux_resource_bin_placements_active_placeholder",
        "resource_bin_placements",
        ["placeholder_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("placeholder_key IS NOT NULL AND ended_at IS NULL"),
    )
    op.create_index(
        "ix_resource_bin_placements_position_active",
        "resource_bin_placements",
        ["position_type", "position_code", "ended_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "handling_operations",
        *_data_columns(),
        sa.Column("operation_key", sa.String(length=240), nullable=False, comment="operation 幂等键"),
        sa.Column("operation_type", sa.String(length=100), nullable=False, comment="operation 类型"),
        sa.Column("object_type", HANDLING_OBJECT_TYPE, nullable=False, comment="对象类型"),
        sa.Column(
            "operation_status",
            HANDLING_OPERATION_STATUS,
            nullable=False,
            server_default="PLANNED",
            comment="operation 状态",
        ),
        sa.Column("workline_id", sa.BigInteger(), nullable=True, comment="可选发起/关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=True, comment="可选工作线编码"),
        sa.Column("material_session_id", sa.BigInteger(), nullable=True, comment="可选关联 WorklineSession.id"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="Trace ID"),
        sa.Column("carrier_type", sa.String(length=50), nullable=True, comment="承运设备类型"),
        sa.Column("carrier_code", sa.String(length=100), nullable=True, comment="承运设备编码"),
        _json_object_column("topology_snapshot_json", comment="拓扑快照"),
        _json_object_column("request_json", comment="内部请求证据"),
        _json_object_column("result_json", comment="完成结果证据"),
        sa.Column("error_code", sa.String(length=100), nullable=True, comment="错误码"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column("requested_at", sa.DateTime(), nullable=True, comment="请求时间"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.ForeignKeyConstraint(["material_session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ux_handling_operations_key", "handling_operations", ["operation_key"], unique=True, schema=SCHEMA)
    op.create_index("ix_handling_operations_status", "handling_operations", ["operation_status"], schema=SCHEMA)
    op.create_index(
        "ix_handling_operations_workline_status",
        "handling_operations",
        ["workline_id", "operation_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "handling_operation_moves",
        *_data_columns(),
        sa.Column("operation_id", sa.BigInteger(), nullable=False, comment="operation.id"),
        sa.Column("operation_key", sa.String(length=240), nullable=False, comment="operation 幂等键"),
        sa.Column("sequence_no", sa.Integer(), nullable=False, comment="operation 内移动序号"),
        sa.Column("object_type", HANDLING_OBJECT_TYPE, nullable=False, comment="移动对象类型"),
        sa.Column("move_status", HANDLING_MOVE_STATUS, nullable=False, server_default="PLANNED", comment="move 状态"),
        sa.Column("rack_code", sa.String(length=100), nullable=True, comment="货架编码"),
        sa.Column("rack_slot_code", sa.String(length=100), nullable=True, comment="货架槽位编码"),
        sa.Column("bin_code", sa.String(length=100), nullable=True, comment="真实料箱编码"),
        sa.Column("placeholder_key", sa.String(length=240), nullable=True, comment="临时占位键"),
        sa.Column("resolved_bin_code", sa.String(length=100), nullable=True, comment="扫码解析后的料箱编码"),
        _json_array_column("candidate_authorized_bin_ids", comment="候选授权料箱集合"),
        sa.Column("source_type", sa.String(length=100), nullable=False, comment="来源类型"),
        sa.Column("source_code", sa.String(length=160), nullable=False, comment="来源编码"),
        sa.Column("target_type", sa.String(length=100), nullable=False, comment="目标类型"),
        sa.Column("target_code", sa.String(length=160), nullable=False, comment="目标编码"),
        sa.Column("carrier_type", sa.String(length=50), nullable=True, comment="承运类型"),
        sa.Column("carrier_code", sa.String(length=100), nullable=True, comment="承运编码"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true"), comment="是否影响成功"),
        _json_object_column("metadata_json", comment="移动证据"),
        sa.ForeignKeyConstraint(["operation_id"], [f"{SCHEMA}.handling_operations.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_handling_moves_operation_sequence",
        "handling_operation_moves",
        ["operation_key", "sequence_no"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ux_handling_moves_active_known_bin",
        "handling_operation_moves",
        ["bin_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "bin_code IS NOT NULL AND move_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')"
        ),
    )
    op.create_index(
        "ux_handling_moves_active_placeholder",
        "handling_operation_moves",
        ["placeholder_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "placeholder_key IS NOT NULL AND move_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')"
        ),
    )
    op.create_index(
        "ix_handling_moves_target_status",
        "handling_operation_moves",
        ["target_type", "target_code", "move_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "system_outbox",
        *_data_columns(),
        sa.Column("operation_id", sa.BigInteger(), nullable=True, comment="关联 HandlingOperation.id"),
        sa.Column("session_id", sa.BigInteger(), nullable=True, comment="可选关联 WorklineSession.id"),
        sa.Column("workline_id", sa.BigInteger(), nullable=True, comment="可选关联 WorkLine.id"),
        sa.Column("dispatch_type", SYSTEM_OUTBOX_DISPATCH_TYPE, nullable=False, comment="派发类型"),
        sa.Column("dispatch_key", sa.String(length=240), nullable=False, comment="派发幂等键"),
        sa.Column("target_type", SYSTEM_OUTBOX_TARGET_TYPE, nullable=False, comment="目标类型"),
        sa.Column("target_code", sa.String(length=240), nullable=False, comment="目标编码"),
        _json_object_column("payload_json", comment="派发负载"),
        sa.Column("status", SYSTEM_OUTBOX_STATUS, nullable=False, server_default="NEW", comment="派发状态"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0", comment="尝试次数"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True, comment="下次重试时间"),
        sa.Column("last_error", sa.Text(), nullable=True, comment="最后错误"),
        sa.Column("sent_at", sa.DateTime(), nullable=True, comment="发送时间"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="结束时间"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="Trace ID"),
        sa.ForeignKeyConstraint(["operation_id"], [f"{SCHEMA}.handling_operations.id"]),
        sa.ForeignKeyConstraint(["session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ux_system_outbox_dispatch_key", "system_outbox", ["dispatch_key"], unique=True, schema=SCHEMA)
    op.create_index("ix_system_outbox_status_retry", "system_outbox", ["status", "next_retry_at"], schema=SCHEMA)
    op.create_index("ix_system_outbox_operation_status", "system_outbox", ["operation_id", "status"], schema=SCHEMA)

    op.create_table(
        "handling_operation_steps",
        *_data_columns(),
        sa.Column("operation_id", sa.BigInteger(), nullable=False, comment="operation.id"),
        sa.Column("operation_key", sa.String(length=240), nullable=False, comment="operation 幂等键"),
        sa.Column("move_id", sa.BigInteger(), nullable=True, comment="关联 move.id"),
        sa.Column("sequence_no", sa.Integer(), nullable=False, comment="operation 内 step 序号"),
        sa.Column("step_key", sa.String(length=240), nullable=False, comment="step 幂等键"),
        sa.Column("step_kind", HANDLING_STEP_KIND, nullable=False, comment="step 类型"),
        sa.Column("step_status", HANDLING_STEP_STATUS, nullable=False, server_default="PLANNED", comment="step 状态"),
        sa.Column("dispatch_key", sa.String(length=240), nullable=True, comment="外部派发键"),
        sa.Column("outbox_id", sa.BigInteger(), nullable=True, comment="关联 system_outbox.id"),
        sa.Column("command_id", sa.BigInteger(), nullable=True, comment="关联 DeviceCommand.id"),
        sa.Column("target_code", sa.String(length=240), nullable=True, comment="目标编码"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="Trace ID"),
        _json_object_column("request_json", comment="请求证据"),
        _json_object_column("callback_json", comment="回调证据"),
        _json_object_column("result_json", comment="结果证据"),
        sa.Column("error_code", sa.String(length=100), nullable=True, comment="错误码"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.ForeignKeyConstraint(["operation_id"], [f"{SCHEMA}.handling_operations.id"]),
        sa.ForeignKeyConstraint(["move_id"], [f"{SCHEMA}.handling_operation_moves.id"]),
        sa.ForeignKeyConstraint(["outbox_id"], [f"{SCHEMA}.system_outbox.id"]),
        sa.ForeignKeyConstraint(["command_id"], [f"{SCHEMA}.device_commands.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ux_handling_steps_key", "handling_operation_steps", ["step_key"], unique=True, schema=SCHEMA)
    op.create_index(
        "ux_handling_steps_dispatch_key",
        "handling_operation_steps",
        ["dispatch_key"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_handling_steps_operation_status",
        "handling_operation_steps",
        ["operation_id", "step_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_handling_steps_kind_status",
        "handling_operation_steps",
        ["step_kind", "step_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("handling_operation_steps", schema=SCHEMA)
    op.drop_table("system_outbox", schema=SCHEMA)
    op.drop_table("handling_operation_moves", schema=SCHEMA)
    op.drop_table("handling_operations", schema=SCHEMA)
    op.drop_table("resource_bin_placements", schema=SCHEMA)
    _drop_constraint_if_exists("resource_state_events", "resourcestateeventtype")
    op.create_check_constraint(
        "resourcestateeventtype",
        "resource_state_events",
        _resource_state_event_type_check(include_bin_placement_events=False),
        schema=SCHEMA,
    )
