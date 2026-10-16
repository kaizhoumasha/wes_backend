"""add_automatic_pickup_authorization_references

Revision ID: 807514a7776b
Revises: c106df25b129
Create Date: 2026-10-17 00:45:35.325943+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "807514a7776b"
down_revision: Union[str, Sequence[str], None] = "c106df25b129"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "automatic_picking_pickups",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
            comment="主键 ID",
        ),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("pickup_id", sa.String(36), nullable=False, unique=True),
        sa.Column("work_id", sa.BigInteger(), nullable=False),
        sa.Column("authorization_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("command_code", sa.String(80), nullable=False, unique=True),
        sa.Column("material_execution_id", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("accept_evidence_id", sa.BigInteger(), nullable=True, unique=True),
        sa.ForeignKeyConstraint(["work_id"], ["wes_biz.automatic_picking_works.id"]),
        sa.ForeignKeyConstraint(["authorization_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["command_code"], ["wes_biz.device_commands.command_code"]),
        sa.ForeignKeyConstraint(["material_execution_id"], ["wes_biz.material_executions.id"]),
        sa.ForeignKeyConstraint(["accept_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.UniqueConstraint("work_id", "authorization_evidence_id", name="ux_automatic_pickup_authorization"),
        schema="wes_biz",
    )
    op.add_column("automatic_picking_works", sa.Column("consumed_pickup_id", sa.String(36)), schema="wes_biz")
    op.add_column("automatic_picking_works", sa.Column("top_removed_evidence_id", sa.BigInteger()), schema="wes_biz")
    op.create_foreign_key(
        "fk_automatic_work_consumed_pickup",
        "automatic_picking_works",
        "automatic_picking_pickups",
        ["consumed_pickup_id"],
        ["pickup_id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_automatic_work_top_removed_evidence",
        "automatic_picking_works",
        "inbound_evidences",
        ["top_removed_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 不能通过降级丢失已消费授权、扫码前身份或当前物理事实。
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM wes_biz.automatic_picking_pickups) OR EXISTS("
            "SELECT 1 FROM wes_biz.automatic_picking_works WHERE consumed_pickup_id IS NOT NULL "
            "OR top_removed_evidence_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("automatic Pickup/current source facts must be retained; downgrade refused")
    op.drop_constraint(
        "fk_automatic_work_consumed_pickup", "automatic_picking_works", schema="wes_biz", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_automatic_work_top_removed_evidence", "automatic_picking_works", schema="wes_biz", type_="foreignkey"
    )
    op.drop_column("automatic_picking_works", "top_removed_evidence_id", schema="wes_biz")
    op.drop_column("automatic_picking_works", "consumed_pickup_id", schema="wes_biz")
    op.drop_table("automatic_picking_pickups", schema="wes_biz")
