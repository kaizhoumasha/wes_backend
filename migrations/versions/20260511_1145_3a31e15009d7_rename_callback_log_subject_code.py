"""rename callback log subject code

Revision ID: 3a31e15009d7
Revises: 7782860238c2
Create Date: 2026-05-11 11:45:32.347844+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3a31e15009d7"
down_revision: Union[str, Sequence[str], None] = "7782860238c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_wes_biz_callback_logs_device_id"), table_name="callback_logs", schema="wes_biz")
    op.alter_column(
        "callback_logs",
        "device_id",
        new_column_name="subject_code",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_callback_logs_subject_code"),
        "callback_logs",
        ["subject_code"],
        unique=False,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wes_biz_callback_logs_subject_code"), table_name="callback_logs", schema="wes_biz")
    op.alter_column(
        "callback_logs",
        "subject_code",
        new_column_name="device_id",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_callback_logs_device_id"),
        "callback_logs",
        ["device_id"],
        unique=False,
        schema="wes_biz",
    )
