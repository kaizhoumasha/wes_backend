"""converge effect state contract

Revision ID: 8fb4b595a85c
Revises: 8db8cbba582c
Create Date: 2026-07-22 10:53:46.323059+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

RUNTIME_SCHEMA = "wes_runtime"
BIZ_SCHEMA = "wes_biz"
RUNTIME_INTENT_TABLE = "runtime_intent_logs"
SYSTEM_OUTBOX_TABLE = "system_outbox"
DISPATCH_ATTEMPT_TABLE = "workline_dispatch_attempts"

RUNTIME_INTENT_STATUS_CONSTRAINT = "ck_runtime_intent_logs_runtime_intent_status"
SYSTEM_OUTBOX_STATUS_CONSTRAINT = "ck_system_outbox_systemoutboxstatus"
DISPATCH_ATTEMPT_STATUS_CONSTRAINT = "ck_workline_dispatch_attempts_dispatchattemptstatus"

# revision identifiers, used by Alembic.
revision: str = "8fb4b595a85c"
down_revision: Union[str, Sequence[str], None] = "8db8cbba582c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """清空未发布旧账本后直接收敛 schema，不保留兼容状态。"""

    # 新状态合同没有旧 identity/status 的无损映射；开发环境显式清账，
    # 避免伪造 dispatch/provider 事实后再施加目标态 NOT NULL 与约束。
    op.execute(
        sa.text(
            """
            TRUNCATE TABLE
                wes_runtime.runtime_intent_logs,
                wes_biz.system_outbox,
                wes_biz.workline_dispatch_attempts
            RESTART IDENTITY CASCADE
            """
        )
    )

    op.drop_index(
        "ix_runtime_intent_log_effect_status",
        table_name=RUNTIME_INTENT_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_runtime_intent_logs_dispatch_status",
        table_name=RUNTIME_INTENT_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    for column_name in (
        "effect_status",
        "dispatch_status",
        "attempt_count",
        "last_error_code",
        "last_error_message",
    ):
        op.drop_column(RUNTIME_INTENT_TABLE, column_name, schema=RUNTIME_SCHEMA)

    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("dispatch_key", sa.String(length=240), nullable=False),
        schema=RUNTIME_SCHEMA,
    )
    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("effect_status", sa.String(length=40), nullable=False),
        schema=RUNTIME_SCHEMA,
    )
    op.create_check_constraint(
        op.f(RUNTIME_INTENT_STATUS_CONSTRAINT),
        RUNTIME_INTENT_TABLE,
        "effect_status IN ('PROPOSED', 'ACCEPTED', 'COMPLETED', 'REJECTED', "
        "'TECHNICAL_FAILED', 'UNKNOWN', 'RECONCILING')",
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ux_runtime_intent_log_dispatch_key",
        RUNTIME_INTENT_TABLE,
        ["dispatch_key"],
        unique=True,
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_runtime_intent_logs_effect_status",
        RUNTIME_INTENT_TABLE,
        ["effect_status"],
        schema=RUNTIME_SCHEMA,
    )

    op.drop_index(
        "ix_system_outbox_blocked_device_head_probe",
        table_name=SYSTEM_OUTBOX_TABLE,
        schema=BIZ_SCHEMA,
    )
    op.drop_constraint(
        op.f(SYSTEM_OUTBOX_STATUS_CONSTRAINT),
        SYSTEM_OUTBOX_TABLE,
        schema=BIZ_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f(SYSTEM_OUTBOX_STATUS_CONSTRAINT),
        SYSTEM_OUTBOX_TABLE,
        "status IN ('NEW', 'DISPATCHING', 'RETRY_WAIT', 'SENT', 'FAILED', 'UNKNOWN', 'CANCELLED')",
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_system_outbox_blocked_device_head_probe",
        SYSTEM_OUTBOX_TABLE,
        [
            "operation_domain",
            "status",
            "dispatch_type",
            "blocked_reason",
            "last_blocked_check_at",
            "blocked_device_id",
            "target_code",
            "created_at",
        ],
        schema=BIZ_SCHEMA,
        postgresql_where=sa.text(
            "status = 'RETRY_WAIT' AND dispatch_type = 'DEVICE_COMMAND' "
            "AND blocked_reason IN ('DEVICE_BUSY', 'DEVICE_STATUS_PRECHECK_WAIT') "
            "AND blocked_at IS NOT NULL AND finished_at IS NULL"
        ),
    )

    op.create_check_constraint(
        op.f(DISPATCH_ATTEMPT_STATUS_CONSTRAINT),
        DISPATCH_ATTEMPT_TABLE,
        "status IN ('DISPATCHING', 'SENT', 'FAILED', 'UNKNOWN', 'CANCELLED')",
        schema=BIZ_SCHEMA,
    )


def downgrade() -> None:
    """清空目标态账本后恢复旧 schema，不保留不兼容状态数据。"""

    op.execute(
        sa.text(
            """
            TRUNCATE TABLE
                wes_runtime.runtime_intent_logs,
                wes_biz.system_outbox,
                wes_biz.workline_dispatch_attempts
            RESTART IDENTITY CASCADE
            """
        )
    )

    op.drop_constraint(
        op.f(DISPATCH_ATTEMPT_STATUS_CONSTRAINT),
        DISPATCH_ATTEMPT_TABLE,
        schema=BIZ_SCHEMA,
        type_="check",
    )

    op.drop_index(
        "ix_system_outbox_blocked_device_head_probe",
        table_name=SYSTEM_OUTBOX_TABLE,
        schema=BIZ_SCHEMA,
    )
    op.drop_constraint(
        op.f(SYSTEM_OUTBOX_STATUS_CONSTRAINT),
        SYSTEM_OUTBOX_TABLE,
        schema=BIZ_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f(SYSTEM_OUTBOX_STATUS_CONSTRAINT),
        SYSTEM_OUTBOX_TABLE,
        "status IN ('NEW', 'DISPATCHING', 'SENT', 'BLOCKED_RESOURCE', 'FAILED', 'CANCELLED')",
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_system_outbox_blocked_device_head_probe",
        SYSTEM_OUTBOX_TABLE,
        [
            "operation_domain",
            "status",
            "dispatch_type",
            "blocked_reason",
            "last_blocked_check_at",
            "blocked_device_id",
            "target_code",
            "created_at",
        ],
        schema=BIZ_SCHEMA,
        postgresql_where=sa.text("status = 'BLOCKED_RESOURCE' AND dispatch_type = 'DEVICE_COMMAND'"),
    )

    op.drop_index(
        "ix_wes_runtime_runtime_intent_logs_effect_status",
        table_name=RUNTIME_INTENT_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    op.drop_index(
        "ux_runtime_intent_log_dispatch_key",
        table_name=RUNTIME_INTENT_TABLE,
        schema=RUNTIME_SCHEMA,
    )
    op.drop_constraint(
        op.f(RUNTIME_INTENT_STATUS_CONSTRAINT),
        RUNTIME_INTENT_TABLE,
        schema=RUNTIME_SCHEMA,
        type_="check",
    )
    op.drop_column(RUNTIME_INTENT_TABLE, "effect_status", schema=RUNTIME_SCHEMA)
    op.drop_column(RUNTIME_INTENT_TABLE, "dispatch_key", schema=RUNTIME_SCHEMA)

    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("effect_status", sa.String(length=40), nullable=True),
        schema=RUNTIME_SCHEMA,
    )
    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("dispatch_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        schema=RUNTIME_SCHEMA,
    )
    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        schema=RUNTIME_SCHEMA,
    )
    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        schema=RUNTIME_SCHEMA,
    )
    op.add_column(
        RUNTIME_INTENT_TABLE,
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_runtime_intent_log_effect_status",
        RUNTIME_INTENT_TABLE,
        ["effect_status"],
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_runtime_intent_logs_dispatch_status",
        RUNTIME_INTENT_TABLE,
        ["dispatch_status"],
        schema=RUNTIME_SCHEMA,
    )
