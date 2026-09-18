"""restore inbound evidence wms and device identity checks

Revision ID: 496bdbaaff26
Revises: e881b50b63b1
Create Date: 2026-09-18 13:50:04.043181+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "496bdbaaff26"
down_revision: Union[str, Sequence[str], None] = "e881b50b63b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """e881b50b63b1 只 drop 了这四条 identity 约束，没有一条被重建；全部补回。"""
    op.create_check_constraint(
        "inbound_evidence_wms_identity_required",
        "inbound_evidences",
        "kind NOT IN ('WMS_EVENT', 'WMS_RESULT') OR (operation IS NOT NULL AND operation_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_device_identity_required",
        "inbound_evidences",
        "kind NOT IN ('DEVICE_EVENT', 'DEVICE_OBSERVATION', 'DEVICE_RESULT') OR device_code IS NOT NULL",
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


def downgrade() -> None:
    """移除本次补回的约束。"""
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_transport_identity_isolated"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_transport_identity_required"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_device_identity_required"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_wms_identity_required"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
