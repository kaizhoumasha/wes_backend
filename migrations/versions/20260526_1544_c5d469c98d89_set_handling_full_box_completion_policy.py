"""set handling full box completion policy

Revision ID: c5d469c98d89
Revises: 3cf0dc588be9
Create Date: 2026-05-26 15:44:49.447745+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d469c98d89"
down_revision: Union[str, Sequence[str], None] = "3cf0dc588be9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"

OPERATION_COMPLETION_POLICY = sa.Enum(
    "CALLBACK_TRUSTED",
    "RESOURCE_PROJECTION_REQUIRED",
    "CALLBACK_PLUS_RECONCILIATION",
    name="operationcompletionpolicy",
    native_enum=False,
    create_constraint=True,
    length=50,
)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = :table_name
                  AND column_name = :column_name
                LIMIT 1
                """
            ).bindparams(schema=SCHEMA, table_name=table_name, column_name=column_name)
        ).scalar()
    )


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = :schema
                  AND indexname = :index_name
                LIMIT 1
                """
            ).bindparams(schema=SCHEMA, index_name=index_name)
        ).scalar()
    )


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
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
    )


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column, schema=SCHEMA)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name, schema=SCHEMA)


def _create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: list[str],
    *,
    postgresql_where: sa.TextClause | None = None,
) -> None:
    if not _index_exists(index_name):
        op.create_index(index_name, table_name, columns, schema=SCHEMA, postgresql_where=postgresql_where)


def _drop_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{SCHEMA}"."{index_name}"'))


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    if _constraint_exists(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, schema=SCHEMA)


def _create_foreign_key_if_missing(
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    *,
    use_alter: bool = False,
) -> None:
    if not _constraint_exists(source_table, constraint_name):
        op.create_foreign_key(
            constraint_name,
            source_table,
            referent_table,
            local_cols,
            remote_cols,
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            use_alter=use_alter,
        )


def _ensure_handling_completion_policy_column() -> None:
    _add_column_if_missing(
        "handling_operations",
        sa.Column(
            "completion_policy",
            OPERATION_COMPLETION_POLICY,
            nullable=False,
            server_default="CALLBACK_TRUSTED",
            comment="完成确认策略",
        ),
    )
    _create_index_if_missing(
        "ix_wes_biz_handling_operations_completion_policy",
        "handling_operations",
        ["completion_policy"],
    )


def _backfill_legacy_system_outbox_operation_identity() -> None:
    if not _column_exists("system_outbox", "operation_id"):
        return

    op.execute(
        sa.text(
            """
            UPDATE wes_biz.system_outbox AS outbox
            SET
                operation_domain = 'HANDLING',
                operation_key = operation.operation_key
            FROM wes_biz.handling_operations AS operation
            WHERE outbox.operation_id = operation.id
              AND outbox.operation_key IS NULL
            """
        )
    )


def _ensure_system_outbox_forward_contract() -> None:
    _drop_index_if_exists("ix_system_outbox_operation_status")
    _drop_constraint_if_exists("system_outbox", "system_outbox_operation_id_fkey")

    _add_column_if_missing(
        "system_outbox",
        sa.Column("device_id", sa.BigInteger(), nullable=True, comment="可选关联 Device.id"),
    )
    _create_foreign_key_if_missing(
        "fk_system_outbox_device_id",
        "system_outbox",
        "devices",
        ["device_id"],
        ["id"],
    )
    _add_column_if_missing(
        "system_outbox",
        sa.Column(
            "operation_domain", sa.String(length=50), nullable=False, server_default="WORKLINE", comment="操作域"
        ),
    )
    _add_column_if_missing(
        "system_outbox",
        sa.Column("operation_key", sa.String(length=240), nullable=True, comment="操作幂等键"),
    )
    _add_column_if_missing(
        "system_outbox",
        sa.Column("blocked_by_reconciliation_session_id", sa.BigInteger(), nullable=True),
    )
    _add_column_if_missing(
        "system_outbox",
        sa.Column("blocked_by_runtime_hold_id", sa.BigInteger(), nullable=True),
    )
    _add_column_if_missing("system_outbox", sa.Column("blocked_device_id", sa.BigInteger(), nullable=True))
    _add_column_if_missing("system_outbox", sa.Column("blocked_workline_id", sa.BigInteger(), nullable=True))
    _add_column_if_missing("system_outbox", sa.Column("blocked_reason", sa.String(length=100), nullable=True))
    _backfill_legacy_system_outbox_operation_identity()
    _drop_column_if_exists("system_outbox", "operation_id")
    _create_foreign_key_if_missing(
        "fk_system_outbox_blocked_by_runtime_hold_id",
        "system_outbox",
        "runtime_holds",
        ["blocked_by_runtime_hold_id"],
        ["id"],
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
        _create_index_if_missing(f"ix_wes_biz_system_outbox_{column_name}", "system_outbox", [column_name])
    _create_index_if_missing(
        "ix_system_outbox_status_retry_created",
        "system_outbox",
        ["status", "next_retry_at", "created_at"],
    )
    _create_index_if_missing(
        "ix_system_outbox_domain_operation",
        "system_outbox",
        ["operation_domain", "operation_key"],
    )
    _create_index_if_missing(
        "ix_system_outbox_context_status",
        "system_outbox",
        ["workline_id", "session_id", "status"],
    )
    _create_index_if_missing(
        "ix_system_outbox_blocked_release",
        "system_outbox",
        ["blocked_reason", "blocked_device_id", "blocked_workline_id"],
    )
    _create_index_if_missing("ix_system_outbox_retention", "system_outbox", ["status", "finished_at"])
    _create_index_if_missing(
        "ix_system_outbox_device_fifo",
        "system_outbox",
        ["dispatch_type", "device_id", "target_code", "status", "created_at"],
        postgresql_where=sa.text("dispatch_type = 'DEVICE_COMMAND'"),
    )


def upgrade() -> None:
    """Upgrade schema."""

    _ensure_handling_completion_policy_column()
    _ensure_system_outbox_forward_contract()

    op.execute(
        sa.text(
            """
            UPDATE wes_biz.handling_operations
            SET completion_policy = 'CALLBACK_PLUS_RECONCILIATION'
            WHERE (
                upper(operation_type) LIKE '%FULL_BOX_EXCHANGE%'
                OR upper(operation_type) LIKE '%FULL_BIN_EXCHANGE%'
                OR upper(operation_type) LIKE '%RACK_BIN_EXCHANGE%'
            )
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute(
        sa.text(
            """
            UPDATE wes_biz.handling_operations
            SET completion_policy = 'CALLBACK_TRUSTED'
            WHERE (
                upper(operation_type) LIKE '%FULL_BOX_EXCHANGE%'
                OR upper(operation_type) LIKE '%FULL_BIN_EXCHANGE%'
                OR upper(operation_type) LIKE '%RACK_BIN_EXCHANGE%'
            )
            """
        )
    )
