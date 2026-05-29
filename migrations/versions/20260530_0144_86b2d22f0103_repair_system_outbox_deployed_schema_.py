"""repair system outbox deployed schema drift

Revision ID: 86b2d22f0103
Revises: c1ea657cb2d7
Create Date: 2026-05-30 01:44:57.879852+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "86b2d22f0103"
down_revision: Union[str, Sequence[str], None] = "c1ea657cb2d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
TABLE_NAME = "system_outbox"


def _column_exists(column_name: str) -> bool:
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
            ).bindparams(schema=SCHEMA, table_name=TABLE_NAME, column_name=column_name)
        ).scalar()
    )


def _constraint_exists(constraint_name: str) -> bool:
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
            ).bindparams(schema=SCHEMA, table_name=TABLE_NAME, constraint_name=constraint_name)
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
                  AND tablename = :table_name
                  AND indexname = :index_name
                LIMIT 1
                """
            ).bindparams(schema=SCHEMA, table_name=TABLE_NAME, index_name=index_name)
        ).scalar()
    )


def _add_column_if_missing(column: sa.Column) -> None:
    if not _column_exists(column.name):
        op.add_column(TABLE_NAME, column, schema=SCHEMA)


def _create_index_if_missing(
    index_name: str,
    columns: Sequence[str],
    *,
    unique: bool = False,
    postgresql_where: str | None = None,
) -> None:
    if _index_exists(index_name):
        return
    kwargs: dict[str, object] = {}
    if postgresql_where is not None:
        kwargs["postgresql_where"] = sa.text(postgresql_where)
    op.create_index(index_name, TABLE_NAME, list(columns), unique=unique, schema=SCHEMA, **kwargs)


def _create_fk_if_missing(
    constraint_name: str,
    local_columns: Sequence[str],
    referent_table: str,
    remote_columns: Sequence[str],
) -> None:
    if _constraint_exists(constraint_name):
        return
    op.create_foreign_key(
        constraint_name,
        TABLE_NAME,
        referent_table,
        list(local_columns),
        list(remote_columns),
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )


def _ensure_system_outbox_columns() -> None:
    """补齐已部署旧 revision 中缺失的最终 system_outbox 合同字段。"""

    _add_column_if_missing(sa.Column("device_id", sa.BigInteger(), nullable=True, comment="可选关联 Device.id"))
    _add_column_if_missing(
        sa.Column(
            "operation_domain",
            sa.String(length=50),
            nullable=False,
            server_default="WORKLINE",
            comment="操作域",
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE wes_biz.system_outbox
            SET operation_domain = 'WORKLINE'
            WHERE operation_domain IS NULL
            """
        )
    )
    op.alter_column(
        TABLE_NAME,
        "operation_domain",
        schema=SCHEMA,
        existing_type=sa.String(length=50),
        nullable=False,
        server_default="WORKLINE",
    )

    _add_column_if_missing(sa.Column("operation_key", sa.String(length=240), nullable=True, comment="操作幂等键"))
    _add_column_if_missing(sa.Column("blocked_by_reconciliation_session_id", sa.BigInteger(), nullable=True))
    _add_column_if_missing(sa.Column("blocked_by_runtime_hold_id", sa.BigInteger(), nullable=True))
    _add_column_if_missing(sa.Column("blocked_device_id", sa.BigInteger(), nullable=True))
    _add_column_if_missing(sa.Column("blocked_workline_id", sa.BigInteger(), nullable=True))
    _add_column_if_missing(sa.Column("blocked_reason", sa.String(length=100), nullable=True))


def _ensure_system_outbox_constraints() -> None:
    _create_fk_if_missing("fk_system_outbox_device_id", ["device_id"], "devices", ["id"])
    _create_fk_if_missing(
        "fk_system_outbox_blocked_by_runtime_hold_id",
        ["blocked_by_runtime_hold_id"],
        "runtime_holds",
        ["id"],
    )


def _ensure_system_outbox_indexes() -> None:
    for column_name in (
        "device_id",
        "operation_domain",
        "operation_key",
        "blocked_by_reconciliation_session_id",
        "blocked_by_runtime_hold_id",
        "blocked_device_id",
        "blocked_workline_id",
    ):
        _create_index_if_missing(f"ix_wes_biz_system_outbox_{column_name}", [column_name])

    _create_index_if_missing(
        "ix_system_outbox_status_retry_created",
        ["status", "next_retry_at", "created_at"],
    )
    _create_index_if_missing("ix_system_outbox_domain_operation", ["operation_domain", "operation_key"])
    _create_index_if_missing("ix_system_outbox_context_status", ["workline_id", "session_id", "status"])
    _create_index_if_missing(
        "ix_system_outbox_blocked_release",
        ["blocked_reason", "blocked_device_id", "blocked_workline_id"],
    )
    _create_index_if_missing("ix_system_outbox_retention", ["status", "finished_at"])
    _create_index_if_missing(
        "ix_system_outbox_device_fifo",
        ["dispatch_type", "device_id", "target_code", "status", "created_at"],
        postgresql_where="dispatch_type = 'DEVICE_COMMAND'",
    )


def upgrade() -> None:
    """Upgrade schema."""
    _ensure_system_outbox_columns()
    _ensure_system_outbox_constraints()
    _ensure_system_outbox_indexes()


def downgrade() -> None:
    """Downgrade schema."""
    # Repair migration: the canonical columns are owned by 745068e173c2 on fresh databases.
    # Downgrading this revision must not remove them from already-correct schemas.
