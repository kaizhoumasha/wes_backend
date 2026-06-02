"""add workline stopped start admission

Revision ID: ec493e8e53a1
Revises: 86b2d22f0103
Create Date: 2026-06-02 00:05:58.895911+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec493e8e53a1"
down_revision: Union[str, Sequence[str], None] = "86b2d22f0103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_STATUS_CONSTRAINT = "worklineruntimestatus"
UPGRADE_RUNTIME_STATUS_VALUES = ("STOPPED", "READY", "RECONCILING", "ESTOPPED")
DOWNGRADE_RUNTIME_STATUS_VALUES = ("READY", "RECONCILING", "ESTOPPED")


def _runtime_status_enum(values: tuple[str, ...]) -> sa.Enum:
    """Match SQLAlchemy Enum(native_enum=False) model style for work_lines.runtime_status."""

    return sa.Enum(
        *values,
        name=RUNTIME_STATUS_CONSTRAINT,
        native_enum=False,
        create_constraint=True,
        length=50,
    )


def _runtime_status_check(values: tuple[str, ...]) -> str:
    allowed_values = ", ".join(f"'{value}'" for value in values)
    return f"runtime_status IN ({allowed_values})"


def _drop_runtime_status_constraint() -> None:
    """Drop SQLAlchemy enum check constraints created with or without naming convention."""

    op.execute("ALTER TABLE wes_biz.work_lines DROP CONSTRAINT IF EXISTS ck_work_lines_worklineruntimestatus")
    op.execute("ALTER TABLE wes_biz.work_lines DROP CONSTRAINT IF EXISTS worklineruntimestatus")


def upgrade() -> None:
    """Add STOPPED runtime default and START admission projection."""

    op.add_column(
        "work_lines",
        sa.Column("start_admission_status", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("start_admission_message", sa.Text(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("start_admission_failed_device_code", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("start_admission_checked_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("last_start_request_id", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("last_start_trace_id", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )

    _drop_runtime_status_constraint()
    op.create_check_constraint(
        RUNTIME_STATUS_CONSTRAINT,
        "work_lines",
        _runtime_status_check(UPGRADE_RUNTIME_STATUS_VALUES),
        schema="wes_biz",
    )
    op.alter_column(
        "work_lines",
        "runtime_status",
        schema="wes_biz",
        existing_type=_runtime_status_enum(UPGRADE_RUNTIME_STATUS_VALUES),
        server_default="STOPPED",
        existing_nullable=False,
    )


def downgrade() -> None:
    """Remove START admission projection and restore READY runtime default."""

    op.alter_column(
        "work_lines",
        "runtime_status",
        schema="wes_biz",
        existing_type=_runtime_status_enum(UPGRADE_RUNTIME_STATUS_VALUES),
        server_default="READY",
        existing_nullable=False,
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM wes_biz.work_lines WHERE runtime_status = 'STOPPED'
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade workline STOPPED runtime_status while STOPPED rows exist';
            END IF;
        END $$;
        """
    )
    _drop_runtime_status_constraint()
    op.create_check_constraint(
        RUNTIME_STATUS_CONSTRAINT,
        "work_lines",
        _runtime_status_check(DOWNGRADE_RUNTIME_STATUS_VALUES),
        schema="wes_biz",
    )

    op.drop_column("work_lines", "last_start_trace_id", schema="wes_biz")
    op.drop_column("work_lines", "last_start_request_id", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_checked_at", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_failed_device_code", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_message", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_status", schema="wes_biz")
