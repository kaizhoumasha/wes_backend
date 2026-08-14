"""对齐 WMS T3 来源结果版本

Revision ID: ce53af214081
Revises: a08d72f135d2
Create Date: 2026-08-14 05:16:22.164321+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ce53af214081"
down_revision: Union[str, Sequence[str], None] = "a08d72f135d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transport_tasks",
        sa.Column(
            "last_applied_wms_outcome_revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema="wes_runtime",
    )
    op.create_check_constraint(
        "transport_last_applied_wms_outcome_revision_valid",
        "transport_tasks",
        "last_applied_wms_outcome_revision >= 0",
        schema="wes_runtime",
    )
    op.add_column(
        "transport_evidence",
        sa.Column("outcome_revision", sa.BigInteger(), nullable=True),
        schema="wes_runtime",
    )
    op.create_check_constraint(
        "transport_evidence_outcome_revision_valid",
        "transport_evidence",
        "outcome_revision IS NULL OR outcome_revision > 0",
        schema="wes_runtime",
    )
    op.create_check_constraint(
        "transport_evidence_outcome_revision_operation_valid",
        "transport_evidence",
        "(operation = 'transport.task.resulted@v1' AND outcome_revision IS NOT NULL) OR "
        "(operation <> 'transport.task.resulted@v1' AND outcome_revision IS NULL)",
        schema="wes_runtime",
    )
    op.create_unique_constraint(
        "ux_transport_evidence_task_outcome_revision",
        "transport_evidence",
        ["transport_task_id", "outcome_revision"],
        schema="wes_runtime",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ux_transport_evidence_task_outcome_revision",
        "transport_evidence",
        schema="wes_runtime",
        type_="unique",
    )
    op.drop_constraint(
        "transport_evidence_outcome_revision_valid",
        "transport_evidence",
        schema="wes_runtime",
        type_="check",
    )
    op.drop_constraint(
        "transport_evidence_outcome_revision_operation_valid",
        "transport_evidence",
        schema="wes_runtime",
        type_="check",
    )
    op.drop_column("transport_evidence", "outcome_revision", schema="wes_runtime")
    op.drop_constraint(
        "transport_last_applied_wms_outcome_revision_valid",
        "transport_tasks",
        schema="wes_runtime",
        type_="check",
    )
    op.drop_column("transport_tasks", "last_applied_wms_outcome_revision", schema="wes_runtime")
