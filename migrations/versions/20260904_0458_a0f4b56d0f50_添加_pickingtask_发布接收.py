"""添加 PickingTask 发布接收

Revision ID: a0f4b56d0f50
Revises: e0da335c057d
Create Date: 2026-09-04 04:58:04.885514+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a0f4b56d0f50"
down_revision: Union[str, Sequence[str], None] = "e0da335c057d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建立统一 PickingTask 队列与入站 evidence 的因果关联。"""
    op.create_table(
        "picking_tasks",
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
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column(
            "task_type",
            sa.Enum(
                "MANUAL",
                "AUTO",
                name="pickingtasktype",
                native_enum=False,
                create_constraint=False,
                length=6,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "PREPARING",
                "EXECUTING",
                "EXECUTION_COMPLETED",
                name="pickingtaskstatus",
                native_enum=False,
                create_constraint=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("queue_revision", sa.BigInteger(), nullable=False),
        sa.Column("dispatch_sequence", sa.BigInteger(), nullable=False),
        sa.Column("not_before_ms", sa.BigInteger(), nullable=True),
        sa.Column("issued_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("issued_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED')",
            name=op.f("ck_picking_tasks_picking_task_status_valid"),
        ),
        sa.CheckConstraint(
            "task_type IN ('MANUAL', 'AUTO')",
            name=op.f("ck_picking_tasks_picking_task_type_valid"),
        ),
        sa.CheckConstraint(
            "queue_revision >= 1",
            name=op.f("ck_picking_tasks_picking_task_queue_revision_positive"),
        ),
        sa.CheckConstraint(
            "dispatch_sequence >= 1",
            name=op.f("ck_picking_tasks_picking_task_dispatch_sequence_positive"),
        ),
        sa.CheckConstraint(
            "issued_at_ms > 0",
            name=op.f("ck_picking_tasks_picking_task_issued_at_positive"),
        ),
        sa.CheckConstraint(
            "not_before_ms IS NULL OR not_before_ms >= 0",
            name=op.f("ck_picking_tasks_picking_task_not_before_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["issued_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_picking_tasks_issued_evidence_id_inbound_evidences"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_picking_tasks")),
        sa.UniqueConstraint("task_id", name="ux_picking_tasks_task_id"),
        sa.UniqueConstraint("issued_evidence_id", name="ux_picking_tasks_issued_evidence"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_picking_tasks_queue",
        "picking_tasks",
        ["status", "not_before_ms", "dispatch_sequence", "id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ux_picking_tasks_queued_dispatch_sequence",
        "picking_tasks",
        ["dispatch_sequence"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'QUEUED'"),
        sqlite_where=sa.text("status = 'QUEUED'"),
    )


def downgrade() -> None:
    """当前未发布业务不提供回退迁移。"""
    raise NotImplementedError("picking_tasks migration does not support downgrade")
