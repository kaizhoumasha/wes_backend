"""allow independent station requests

Revision ID: f7cf0cd8c6d4
Revises: 910b24bb0e05
Create Date: 2026-09-11 11:25:43.869832+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7cf0cd8c6d4"
down_revision: Union[str, Sequence[str], None] = "910b24bb0e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_kind_valid"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_kind_valid"),
        "inbound_evidences",
        "kind IN ('DEVICE_EVENT', 'DEVICE_OBSERVATION', 'DEVICE_RESULT', "
        "'TRANSPORT_RESULT', 'WMS_EVENT', 'WMS_RESULT')",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_device_identity_required"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_device_identity_required"),
        "inbound_evidences",
        "kind NOT IN ('DEVICE_EVENT', 'DEVICE_OBSERVATION', 'DEVICE_RESULT') OR device_code IS NOT NULL",
        schema="wes_biz",
    )
    op.drop_index("ux_material_executions_active_trace", table_name="material_executions", schema="wes_biz")
    op.create_index(
        "ux_material_executions_admission_evidence",
        "material_executions",
        ["admission_evidence_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("admission_evidence_id IS NOT NULL"),
    )
    op.create_index(
        "ux_device_commands_dispatching_device",
        "device_commands",
        ["device_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'DISPATCHING'"),
    )
    op.drop_index("ux_device_commands_unclosed_device", table_name="device_commands", schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        "ux_device_commands_unclosed_device",
        "device_commands",
        ["device_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')"),
    )
    op.drop_index(
        "ux_device_commands_dispatching_device",
        table_name="device_commands",
        schema="wes_biz",
    )
    op.drop_index(
        "ux_material_executions_admission_evidence",
        table_name="material_executions",
        schema="wes_biz",
    )
    op.create_index(
        "ux_material_executions_active_trace",
        "material_executions",
        ["material_trace_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status <> 'CLOSED'"),
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_device_identity_required"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_device_identity_required"),
        "inbound_evidences",
        "kind NOT IN ('DEVICE_EVENT', 'DEVICE_RESULT') OR device_code IS NOT NULL",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_kind_valid"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_kind_valid"),
        "inbound_evidences",
        "kind IN ('DEVICE_EVENT', 'DEVICE_RESULT', 'TRANSPORT_RESULT', 'WMS_EVENT', 'WMS_RESULT')",
        schema="wes_biz",
    )
