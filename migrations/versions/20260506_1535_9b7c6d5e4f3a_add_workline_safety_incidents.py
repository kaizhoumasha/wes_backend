"""add workline safety incidents

Revision ID: 9b7c6d5e4f3a
Revises: c0d1e2f3a4b5
Create Date: 2026-05-06 15:35:00.000000+08:00

软件侧 WorkLine 急停治理：
- work_lines 保存当前安全投影，供入口和派发快速阻断。
- workline_safety_incidents 保存急停事件、排空证据和恢复 checklist 审计。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9b7c6d5e4f3a"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add WorkLine safety projection and incident audit table."""

    op.add_column(
        "work_lines",
        sa.Column(
            "runtime_status",
            sa.Enum(
                "READY",
                "ESTOPPED",
                name="worklineruntimestatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            server_default="READY",
            nullable=False,
        ),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("active_safety_incident_id", sa.BigInteger(), nullable=True),
        schema="wes_biz",
    )
    op.add_column("work_lines", sa.Column("stopped_at", sa.DateTime(), nullable=True), schema="wes_biz")
    op.add_column("work_lines", sa.Column("stopped_reason", sa.String(length=200), nullable=True), schema="wes_biz")
    op.add_column("work_lines", sa.Column("resumed_at", sa.DateTime(), nullable=True), schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_work_lines_runtime_status"),
        "work_lines",
        ["runtime_status"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_work_lines_active_safety_incident_id"),
        "work_lines",
        ["active_safety_incident_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_work_lines_stopped_at"),
        "work_lines",
        ["stopped_at"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_work_lines_resumed_at"),
        "work_lines",
        ["resumed_at"],
        unique=False,
        schema="wes_biz",
    )

    op.create_table(
        "workline_safety_incidents",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("workline_id", sa.BigInteger(), nullable=True),
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
        sa.Column("source_inbox_id", sa.BigInteger(), nullable=True),
        sa.Column("source_device_id", sa.BigInteger(), nullable=True),
        sa.Column("source_command_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "trigger_payload_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "evidence_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "release_evidence_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "recovery_check_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column("drain_status", sa.String(length=50), nullable=False),
        sa.Column(
            "drain_error_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column("cleared_at", sa.DateTime(), nullable=True),
        sa.Column("cleared_by", sa.BigInteger(), nullable=True),
        sa.Column("clear_reason", sa.Text(), nullable=True),
        sa.Column(
            "resolution_inputs_tried",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "missing_identifiers",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("next_action", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_id"),
        "workline_safety_incidents",
        ["id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_workline_id"),
        "workline_safety_incidents",
        ["workline_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_status"),
        "workline_safety_incidents",
        ["status"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_event_type"),
        "workline_safety_incidents",
        ["event_type"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_inbox_id"),
        "workline_safety_incidents",
        ["source_inbox_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_device_id"),
        "workline_safety_incidents",
        ["source_device_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_command_id"),
        "workline_safety_incidents",
        ["source_command_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_drain_status"),
        "workline_safety_incidents",
        ["drain_status"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_cleared_at"),
        "workline_safety_incidents",
        ["cleared_at"],
        unique=False,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Remove WorkLine safety projection and incident audit table."""

    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_cleared_at"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_drain_status"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_command_id"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_device_id"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_inbox_id"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_event_type"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_status"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_workline_id"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_id"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_table("workline_safety_incidents", schema="wes_biz")

    op.drop_index(op.f("ix_wes_biz_work_lines_resumed_at"), table_name="work_lines", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_work_lines_stopped_at"), table_name="work_lines", schema="wes_biz")
    op.drop_index(
        op.f("ix_wes_biz_work_lines_active_safety_incident_id"),
        table_name="work_lines",
        schema="wes_biz",
    )
    op.drop_index(op.f("ix_wes_biz_work_lines_runtime_status"), table_name="work_lines", schema="wes_biz")
    op.drop_column("work_lines", "resumed_at", schema="wes_biz")
    op.drop_column("work_lines", "stopped_reason", schema="wes_biz")
    op.drop_column("work_lines", "stopped_at", schema="wes_biz")
    op.drop_column("work_lines", "active_safety_incident_id", schema="wes_biz")
    op.drop_column("work_lines", "runtime_status", schema="wes_biz")
