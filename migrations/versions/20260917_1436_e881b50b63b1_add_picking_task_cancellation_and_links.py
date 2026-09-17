"""add_picking_task_cancellation_and_links

Revision ID: e881b50b63b1
Revises: 4ce43f66c610
Create Date: 2026-09-17 14:36:09.754967+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e881b50b63b1"
down_revision: Union[str, Sequence[str], None] = "4ce43f66c610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加任务取消边界和可直接排查的任务关联。"""
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_status_valid"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "picking_task_status_valid",
        "picking_tasks",
        "status IN ('QUEUED', 'PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED', 'CANCELLED', 'ARCHIVED')",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_binding_matches_status"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.alter_column("picking_tasks", "workline_id", nullable=False, schema="wes_biz")
    op.drop_index("ix_picking_tasks_queue", table_name="picking_tasks", schema="wes_biz")
    op.create_index(
        "ix_picking_tasks_queue",
        "picking_tasks",
        ["workline_id", "task_type", "dispatch_sequence", "id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'QUEUED'"),
    )

    for table_name, foreign_key_name in (
        ("direct_pick_executions", "fk_direct_pick_cancelled_evidence"),
        ("picking_task_bin_source_racks", "fk_picking_bin_source_cancelled_evidence"),
    ):
        op.add_column(table_name, sa.Column("cancelled_evidence_id", sa.BigInteger(), nullable=True), schema="wes_biz")
        op.create_foreign_key(
            foreign_key_name,
            table_name,
            "inbound_evidences",
            ["cancelled_evidence_id"],
            ["id"],
            source_schema="wes_biz",
            referent_schema="wes_biz",
        )

    op.add_column("inbound_evidences", sa.Column("picking_task_id", sa.BigInteger(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        "fk_inbound_evidences_picking_task_id_picking_tasks",
        "inbound_evidences",
        "picking_tasks",
        ["picking_task_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        "ix_inbound_evidences_picking_task_timeline",
        "inbound_evidences",
        ["picking_task_id", "received_at", "id"],
        unique=False,
        schema="wes_biz",
    )

    op.add_column(
        "transport_decision_bindings",
        sa.Column("picking_task_id", sa.BigInteger(), nullable=True),
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_transport_decision_bindings_picking_task_id_picking_tasks",
        "transport_decision_bindings",
        "picking_tasks",
        ["picking_task_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        "ix_transport_decision_bindings_task_step",
        "transport_decision_bindings",
        ["workline_id", "picking_task_id", "step"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ix_transport_decision_bindings_picking_task",
        "transport_decision_bindings",
        ["picking_task_id"],
        unique=False,
        schema="wes_biz",
    )

    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_wms_identity_required"),
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
        op.f("ck_inbound_evidences_inbound_evidence_transport_identit_0680"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_inbound_evidences_inbound_evidence_transport_identit_bdec"),
        "inbound_evidences",
        schema="wes_biz",
        type_="check",
    )


def downgrade() -> None:
    """移除本次未发布合同。"""
    op.create_check_constraint(
        "inbound_evidence_transport_identity_isolated",
        "inbound_evidences",
        "kind <> 'TRANSPORT_RESULT' OR "
        "(device_code IS NULL AND command_code IS NULL AND operation IS NULL AND operation_id IS NULL)",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_transport_identity_required",
        "inbound_evidences",
        "(kind = 'TRANSPORT_RESULT') = (transport_task_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_device_identity_required",
        "inbound_evidences",
        "kind NOT IN ('DEVICE_EVENT', 'DEVICE_OBSERVATION', 'DEVICE_RESULT') OR device_code IS NOT NULL",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_wms_identity_required",
        "inbound_evidences",
        "kind NOT IN ('WMS_EVENT', 'WMS_RESULT') OR (operation IS NOT NULL AND operation_id IS NOT NULL)",
        schema="wes_biz",
    )

    op.drop_index(
        "ix_transport_decision_bindings_picking_task",
        table_name="transport_decision_bindings",
        schema="wes_biz",
    )
    op.drop_index(
        "ix_transport_decision_bindings_task_step",
        table_name="transport_decision_bindings",
        schema="wes_biz",
    )
    op.drop_constraint(
        "fk_transport_decision_bindings_picking_task_id_picking_tasks",
        "transport_decision_bindings",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("transport_decision_bindings", "picking_task_id", schema="wes_biz")

    op.drop_index(
        "ix_inbound_evidences_picking_task_timeline",
        table_name="inbound_evidences",
        schema="wes_biz",
    )
    op.drop_constraint(
        "fk_inbound_evidences_picking_task_id_picking_tasks",
        "inbound_evidences",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("inbound_evidences", "picking_task_id", schema="wes_biz")

    for table_name, foreign_key_name in (
        ("picking_task_bin_source_racks", "fk_picking_bin_source_cancelled_evidence"),
        ("direct_pick_executions", "fk_direct_pick_cancelled_evidence"),
    ):
        op.drop_constraint(
            foreign_key_name,
            table_name,
            schema="wes_biz",
            type_="foreignkey",
        )
        op.drop_column(table_name, "cancelled_evidence_id", schema="wes_biz")

    op.drop_index("ix_picking_tasks_queue", table_name="picking_tasks", schema="wes_biz")
    op.create_index(
        "ix_picking_tasks_queue",
        "picking_tasks",
        ["task_type", "dispatch_sequence", "id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.alter_column("picking_tasks", "workline_id", nullable=True, schema="wes_biz")
    op.create_check_constraint(
        "picking_task_binding_matches_status",
        "picking_tasks",
        "(status = 'QUEUED' AND workline_id IS NULL) OR "
        "(status IN ('PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED', 'ARCHIVED') AND workline_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_status_valid"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "picking_task_status_valid",
        "picking_tasks",
        "status IN ('QUEUED', 'PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED', 'ARCHIVED')",
        schema="wes_biz",
    )
