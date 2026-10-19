"""add automatic drain rack handoff

Revision ID: 40e50581119c
Revises: a243886b4a58
Create Date: 2026-10-20 02:09:00.892055+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "40e50581119c"
down_revision: Union[str, Sequence[str], None] = "a243886b4a58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automatic_picking_drain_racks",
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
        sa.Column("drain_id", sa.BigInteger(), nullable=False),
        sa.Column("rack_id", sa.String(160), nullable=False),
        sa.Column("binding_id", sa.BigInteger(), nullable=False),
        sa.Column("arrival_evidence_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automatic_picking_drain_racks")),
        sa.UniqueConstraint("drain_id", "rack_id", name="ux_automatic_drain_rack"),
        sa.UniqueConstraint("binding_id", name=op.f("uq_automatic_picking_drain_racks_binding_id")),
        sa.ForeignKeyConstraint(
            ["drain_id"],
            ["wes_biz.automatic_picking_drains.id"],
            name=op.f("fk_automatic_picking_drain_racks_drain_id_automatic_picking_drains"),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["wes_biz.transport_decision_bindings.id"],
            name=op.f("fk_automatic_picking_drain_racks_binding_id_transport_decision_bindings"),
        ),
        sa.ForeignKeyConstraint(
            ["arrival_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_automatic_picking_drain_racks_arrival_evidence_id_inbound_evidences"),
        ),
        schema="wes_biz",
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_drain_racks)")).scalar():
        raise RuntimeError("cannot discard automatic drain rack execution identities")
    op.drop_table("automatic_picking_drain_racks", schema="wes_biz")
