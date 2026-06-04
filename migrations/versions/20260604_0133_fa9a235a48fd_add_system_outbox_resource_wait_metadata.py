"""add system outbox resource wait metadata

Revision ID: fa9a235a48fd
Revises: 1bda271cfeb5
Create Date: 2026-06-04 01:33:49.889366+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa9a235a48fd"
down_revision: Union[str, Sequence[str], None] = "1bda271cfeb5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
TABLE_NAME = "system_outbox"
_BLOCKED_DEVICE_HEAD_PROBE_WHERE = sa.text("status = 'BLOCKED_RESOURCE' AND dispatch_type = 'DEVICE_COMMAND'")


def _json_empty_object_default() -> sa.TextClause:
    if op.get_bind().dialect.name == "postgresql":
        return sa.text("'{}'::json")
    return sa.text("'{}'")


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(TABLE_NAME, sa.Column("blocked_at", sa.DateTime(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE_NAME, sa.Column("last_blocked_check_at", sa.DateTime(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE_NAME,
        sa.Column("blocked_check_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE_NAME,
        sa.Column("blocked_detail_json", sa.JSON(), nullable=False, server_default=_json_empty_object_default()),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_system_outbox_blocked_device_head_probe",
        TABLE_NAME,
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
        schema=SCHEMA,
        postgresql_where=_BLOCKED_DEVICE_HEAD_PROBE_WHERE,
        sqlite_where=_BLOCKED_DEVICE_HEAD_PROBE_WHERE,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index("ix_system_outbox_blocked_device_head_probe", table_name=TABLE_NAME, schema=SCHEMA)
    op.drop_column(TABLE_NAME, "blocked_detail_json", schema=SCHEMA)
    op.drop_column(TABLE_NAME, "blocked_check_count", schema=SCHEMA)
    op.drop_column(TABLE_NAME, "last_blocked_check_at", schema=SCHEMA)
    op.drop_column(TABLE_NAME, "blocked_at", schema=SCHEMA)
