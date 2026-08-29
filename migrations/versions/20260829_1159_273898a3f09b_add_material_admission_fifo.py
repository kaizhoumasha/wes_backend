"""add material admission fifo

Revision ID: 273898a3f09b
Revises: baf328359533
Create Date: 2026-08-29 11:59:24.190466+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "273898a3f09b"
down_revision: Union[str, Sequence[str], None] = "baf328359533"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """冻结新 admission 顺序；已有闭合历史允许保留空 admission。"""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM wes_biz.material_executions
                WHERE status <> 'CLOSED' LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'material admission FIFO cutover requires no active MaterialExecution';
            END IF;
        END
        $$
        """
    )
    op.add_column(
        "material_executions",
        sa.Column("admission_received_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "material_executions",
        sa.Column("admission_evidence_id", sa.BigInteger(), nullable=True),
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_material_executions_admission_evidence_id_inbound_evidences",
        "material_executions",
        "inbound_evidences",
        ["admission_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        "material_execution_active_admission_required",
        "material_executions",
        "status = 'CLOSED' OR (admission_received_at IS NOT NULL AND admission_evidence_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.create_index(
        "ix_material_executions_active_fifo",
        "material_executions",
        [
            "workline_id",
            "line_run_epoch_id",
            "admission_received_at",
            "admission_evidence_id",
            "id",
        ],
        schema="wes_biz",
        postgresql_where=sa.text("status <> 'CLOSED'"),
    )


def downgrade() -> None:
    op.drop_index("ix_material_executions_active_fifo", table_name="material_executions", schema="wes_biz")
    op.drop_constraint(
        "material_execution_active_admission_required",
        "material_executions",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        "fk_material_executions_admission_evidence_id_inbound_evidences",
        "material_executions",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("material_executions", "admission_evidence_id", schema="wes_biz")
    op.drop_column("material_executions", "admission_received_at", schema="wes_biz")
