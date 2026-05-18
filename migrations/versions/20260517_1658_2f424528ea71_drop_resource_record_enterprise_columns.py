"""drop resource record enterprise columns

Revision ID: 2f424528ea71
Revises: e9ec8588062f
Create Date: 2026-05-17 16:58:13.395177+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f424528ea71"
down_revision: Union[str, Sequence[str], None] = "e9ec8588062f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RESOURCE_RECORD_TABLES = (
    "resource_state_events",
    "resource_wms_writeback_evidence",
    "resource_rack_release_bin_snapshots",
    "resource_bin_content_snapshots",
    "resource_bin_content_snapshot_items",
)

ENTERPRISE_STATE_COLUMNS = ("updated_by", "created_by", "version")


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in RESOURCE_RECORD_TABLES:
        for column_name in ENTERPRISE_STATE_COLUMNS:
            op.drop_column(table_name, column_name, schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in RESOURCE_RECORD_TABLES:
        op.add_column(
            table_name,
            sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
            schema="wes_biz",
        )
        op.add_column(
            table_name,
            sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
            schema="wes_biz",
        )
        op.add_column(
            table_name,
            sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
            schema="wes_biz",
        )
