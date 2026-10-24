"""自动最终转运架当前离场义务

Revision ID: f5c962df755c
Revises: 6781e26d6e19
Create Date: 2026-10-23 04:24:00.555680+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5c962df755c"
down_revision: Union[str, Sequence[str], None] = "6781e26d6e19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automatic_picking_final_target_racks",
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
        sa.Column("workline_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("picking_task_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("rack_id", sa.String(length=100), nullable=False),
        sa.Column("root_transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("arrival_transport_task_id", sa.String(length=80), nullable=True),
        sa.Column("departure_confirmation_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("departure_retry_at", sa.DateTime(), nullable=True),
        sa.Column("departure_transport_task_id", sa.String(length=80), nullable=True),
        sa.Column("closed_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.ForeignKeyConstraint(
            ["arrival_transport_task_id"],
            ["wes_biz.transport_tasks.transport_task_id"],
            name=op.f("fk_automatic_picking_final_target_racks_arrival_transport_task_id_transport_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["closed_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_automatic_picking_final_target_racks_closed_evidence_id_inbound_evidences"),
        ),
        sa.ForeignKeyConstraint(
            ["departure_confirmation_id"],
            ["wes_biz.wms_confirmations.id"],
            name=op.f("fk_automatic_picking_final_target_racks_departure_confirmation_id_wms_confirmations"),
        ),
        sa.ForeignKeyConstraint(
            ["departure_transport_task_id"],
            ["wes_biz.transport_tasks.transport_task_id"],
            name=op.f("fk_automatic_picking_final_target_racks_departure_transport_task_id_transport_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["picking_task_id"],
            ["wes_biz.picking_tasks.id"],
            name=op.f("fk_automatic_picking_final_target_racks_picking_task_id_picking_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["workline_id"],
            ["wes_biz.work_lines.id"],
            name=op.f("fk_automatic_picking_final_target_racks_workline_id_work_lines"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automatic_picking_final_target_racks")),
        sa.UniqueConstraint(
            "departure_transport_task_id",
            name=op.f("uq_automatic_picking_final_target_racks_departure_transport_task_id"),
        ),
        sa.UniqueConstraint("picking_task_id", name=op.f("uq_automatic_picking_final_target_racks_picking_task_id")),
        schema="wes_biz",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.select(sa.exists().select_from(sa.table("automatic_picking_final_target_racks", schema="wes_biz"))))
        .scalar()
    ):
        raise RuntimeError("cannot discard current final-target departure obligations")
    op.drop_table("automatic_picking_final_target_racks", schema="wes_biz")
