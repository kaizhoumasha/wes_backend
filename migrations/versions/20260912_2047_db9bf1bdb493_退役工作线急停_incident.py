"""退役工作线急停 incident

Revision ID: db9bf1bdb493
Revises: b0edce3425ef
Create Date: 2026-09-12 20:47:10.296217+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "db9bf1bdb493"
down_revision: Union[str, Sequence[str], None] = "b0edce3425ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除 WES 急停 incident 及其运行态投影引用。"""
    op.drop_index(
        "ix_wrt_status_proj_safety_incident",
        table_name="workline_runtime_status_projections",
        schema="wes_runtime",
    )
    op.drop_column(
        "workline_runtime_status_projections",
        "active_safety_incident_id",
        schema="wes_runtime",
    )
    op.drop_constraint(
        "ck_wrt_status_proj_status",
        "workline_runtime_status_projections",
        schema="wes_runtime",
        type_="check",
    )
    op.create_check_constraint(
        "ck_wrt_status_proj_status",
        "workline_runtime_status_projections",
        "runtime_status IN ('READY', 'STOPPED', 'STARTING', 'RECONCILING')",
        schema="wes_runtime",
    )
    op.drop_table("workline_safety_incidents", schema="wes_biz")


def downgrade() -> None:
    """只恢复旧结构，不伪造已删除的 incident 历史。"""
    op.drop_constraint(
        "ck_wrt_status_proj_status",
        "workline_runtime_status_projections",
        schema="wes_runtime",
        type_="check",
    )
    op.create_check_constraint(
        "ck_wrt_status_proj_status",
        "workline_runtime_status_projections",
        "runtime_status IN ('READY', 'STOPPED', 'STARTING', 'ESTOPPED', 'RECONCILING')",
        schema="wes_runtime",
    )
    op.add_column(
        "workline_runtime_status_projections",
        sa.Column(
            "active_safety_incident_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_wrt_status_proj_safety_incident",
        "workline_runtime_status_projections",
        ["active_safety_incident_id"],
        unique=False,
        schema="wes_runtime",
    )
    op.create_table(
        "workline_safety_incidents",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
            comment="主键 ID",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("workline_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "CLEARED",
                "UNRESOLVED",
                name="worklinesafetyincidentstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("source_inbox_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("source_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("source_device_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("source_command_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("trigger_payload_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("release_evidence_json", sa.JSON(), nullable=False),
        sa.Column("recovery_check_json", sa.JSON(), nullable=False),
        sa.Column("drain_status", sa.String(length=50), nullable=False),
        sa.Column("drain_error_json", sa.JSON(), nullable=False),
        sa.Column("cleared_at", sa.DateTime(), nullable=True),
        sa.Column("cleared_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("clear_reason", sa.Text(), nullable=True),
        sa.Column("resolution_inputs_tried", sa.JSON(), nullable=False),
        sa.Column("missing_identifiers", sa.JSON(), nullable=False),
        sa.Column("next_action", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_workline_safety_incidents_source_evidence_id_inbound_evidences"),
        ),
        sa.ForeignKeyConstraint(
            ["workline_id"],
            ["wes_biz.work_lines.id"],
            name=op.f("fk_workline_safety_incidents_workline_id_work_lines"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workline_safety_incidents")),
        schema="wes_biz",
    )
    for column in (
        "cleared_at",
        "drain_status",
        "event_type",
        "source_command_id",
        "source_device_id",
        "source_evidence_id",
        "source_inbox_id",
        "status",
        "workline_id",
    ):
        op.create_index(
            op.f(f"ix_wes_biz_workline_safety_incidents_{column}"),
            "workline_safety_incidents",
            [column],
            unique=False,
            schema="wes_biz",
        )
