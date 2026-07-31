"""add provider identity to WMS query evidence

Revision ID: bba1942e9ea8
Revises: 5d251fdbb1e8
Create Date: 2026-07-23 19:33:15.604803+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

SCHEMA = "wes_biz"
EVIDENCE_TABLE = "wms_call_evidence"

# revision identifiers, used by Alembic.
revision: str = "bba1942e9ea8"
down_revision: Union[str, Sequence[str], None] = "5d251fdbb1e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        EVIDENCE_TABLE,
        sa.Column(
            "provider_profile_identity",
            sa.String(length=240),
            nullable=True,
            comment="同步 QUERY 的冻结 provider profile identity；异步摘要不适用",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wms_call_evidence_provider_operation_started",
        EVIDENCE_TABLE,
        ["provider_profile_identity", "operation_name", "started_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wms_call_evidence_provider_operation_started",
        table_name=EVIDENCE_TABLE,
        schema=SCHEMA,
    )
    op.drop_column(EVIDENCE_TABLE, "provider_profile_identity", schema=SCHEMA)
