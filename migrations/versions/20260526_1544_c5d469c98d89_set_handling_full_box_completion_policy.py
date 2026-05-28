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
TABLE = "handling_operations"
COMPLETION_POLICY_COLUMN = "completion_policy"
COMPLETION_POLICY_INDEX = "ix_wes_biz_handling_operations_completion_policy"
COMPLETION_POLICY_CONSTRAINT = "operationcompletionpolicy"


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
            ).bindparams(schema=SCHEMA, table_name=TABLE, column_name=column_name)
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
            ).bindparams(schema=SCHEMA, table_name=TABLE, constraint_name=constraint_name)
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
            ).bindparams(schema=SCHEMA, table_name=TABLE, index_name=index_name)
        ).scalar()
    )


def _ensure_completion_policy_column() -> None:
    if not _column_exists(COMPLETION_POLICY_COLUMN):
        op.add_column(
            TABLE,
            sa.Column(
                COMPLETION_POLICY_COLUMN,
                sa.String(length=50),
                server_default="CALLBACK_TRUSTED",
                nullable=False,
                comment="完成确认策略",
            ),
            schema=SCHEMA,
        )

    if not _constraint_exists(COMPLETION_POLICY_CONSTRAINT):
        op.create_check_constraint(
            COMPLETION_POLICY_CONSTRAINT,
            TABLE,
            """
            completion_policy IN (
                'CALLBACK_TRUSTED',
                'RESOURCE_PROJECTION_REQUIRED',
                'CALLBACK_PLUS_RECONCILIATION'
            )
            """,
            schema=SCHEMA,
        )

    if not _index_exists(COMPLETION_POLICY_INDEX):
        op.create_index(
            COMPLETION_POLICY_INDEX,
            TABLE,
            [COMPLETION_POLICY_COLUMN],
            schema=SCHEMA,
        )


def upgrade() -> None:
    """Upgrade schema."""

    _ensure_completion_policy_column()
    op.execute(
        sa.text(
            """
            UPDATE wes_biz.handling_operations
            SET completion_policy = 'CALLBACK_PLUS_RECONCILIATION'
            WHERE upper(operation_type) LIKE '%FULL_BOX_EXCHANGE%'
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
            WHERE upper(operation_type) LIKE '%FULL_BOX_EXCHANGE%'
            """
        )
    )
