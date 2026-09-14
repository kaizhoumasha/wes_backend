"""支持工作线业务设备结果领取

Revision ID: 9d275378a869
Revises: 6cf85c1760e4
Create Date: 2026-09-13 23:30:01.716959+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d275378a869"
down_revision: Union[str, Sequence[str], None] = "6cf85c1760e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_inbound_evidences_decision_eligible", table_name="inbound_evidences", schema="wes_biz")
    op.create_index(
        "ix_inbound_evidences_decision_eligible",
        "inbound_evidences",
        ["decision_next_attempt_at", "decision_claim_expires_at", "received_at", "id"],
        schema="wes_biz",
        postgresql_where=sa.text("apply_status = 'APPLIED' AND published_at IS NULL"),
        sqlite_where=sa.text("apply_status = 'APPLIED' AND published_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_inbound_evidences_decision_eligible", table_name="inbound_evidences", schema="wes_biz")
    predicate = (
        "apply_status = 'APPLIED' AND published_at IS NULL "
        "AND NOT (kind = 'DEVICE_RESULT' AND material_execution_id IS NULL)"
    )
    op.create_index(
        "ix_inbound_evidences_decision_eligible",
        "inbound_evidences",
        ["decision_next_attempt_at", "decision_claim_expires_at", "received_at", "id"],
        schema="wes_biz",
        postgresql_where=sa.text(predicate),
        sqlite_where=sa.text(predicate),
    )
