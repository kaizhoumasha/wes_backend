"""add execution session correlation

Revision ID: c0bccb9de6f3
Revises: 0e9de1e6c7e3
Create Date: 2026-06-26 11:40:31.623337+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0bccb9de6f3"
down_revision: Union[str, Sequence[str], None] = "0e9de1e6c7e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_runtime"
EXECUTION_CORRELATION_SESSION_FK = "fk_exec_corr_session"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "execution_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("manifest_version", sa.String(length=60), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_sessions_workline_id",
        "execution_sessions",
        ["workline_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "execution_correlations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("execution_session_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=120), nullable=False),
        sa.Column("source_event_id", sa.String(length=160), nullable=True),
        sa.Column("business_owner_key", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_session_id"],
            ["wes_runtime.execution_sessions.id"],
            name=EXECUTION_CORRELATION_SESSION_FK,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_correlations_correlation_id",
        "execution_correlations",
        ["correlation_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_correlations_trace_id",
        "execution_correlations",
        ["trace_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wes_runtime_execution_correlations_trace_id",
        table_name="execution_correlations",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_execution_correlations_correlation_id",
        table_name="execution_correlations",
        schema=SCHEMA,
    )
    op.drop_table("execution_correlations", schema=SCHEMA)

    op.drop_index(
        "ix_wes_runtime_execution_sessions_workline_id",
        table_name="execution_sessions",
        schema=SCHEMA,
    )
    op.drop_table("execution_sessions", schema=SCHEMA)
