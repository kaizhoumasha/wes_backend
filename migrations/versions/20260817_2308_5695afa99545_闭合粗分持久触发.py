"""闭合粗分持久触发

Revision ID: 5695afa99545
Revises: 72ecc4fd560f
Create Date: 2026-08-17 23:08:27.379300+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5695afa99545"
down_revision: Union[str, Sequence[str], None] = "72ecc4fd560f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """直接替换批量恢复绑定，并增加 Transport evidence 身份。"""

    op.add_column(
        "inbound_evidences",
        sa.Column("transport_task_id", sa.String(length=120), nullable=True),
        schema="wes_biz",
    )
    op.drop_constraint(
        "inbound_evidence_kind_valid",
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "inbound_evidence_kind_valid",
        "inbound_evidences",
        "kind IN ('DEVICE_EVENT', 'DEVICE_RESULT', 'TRANSPORT_RESULT', 'WMS_EVENT', 'WMS_RESULT')",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_transport_identity_required",
        "inbound_evidences",
        "(kind = 'TRANSPORT_RESULT') = (transport_task_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_transport_identity_isolated",
        "inbound_evidences",
        "kind <> 'TRANSPORT_RESULT' OR "
        "(device_code IS NULL AND command_code IS NULL AND operation IS NULL AND operation_id IS NULL)",
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_inbound_evidences_transport_task_id"),
        "inbound_evidences",
        ["transport_task_id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_inbound_evidences_transport_task",
        "inbound_evidences",
        ["transport_task_id", "kind"],
        schema="wes_biz",
    )
    op.drop_table("inbound_evidence_execution_bindings", schema="wes_biz")


def downgrade() -> None:
    raise NotImplementedError("Phase 8 direct cutover 不支持恢复批量 reconciliation binding")
