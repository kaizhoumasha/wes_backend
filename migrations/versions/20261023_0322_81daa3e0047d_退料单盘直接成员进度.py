"""退料单盘直接成员归属与当前完成事实。"""

import sqlalchemy as sa
from alembic import op

revision = "81daa3e0047d"
down_revision = "8c9d01122445"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "automatic_picking_direct_picks",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="主键 ID"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("workline_id", sa.BigInteger(), sa.ForeignKey("wes_biz.work_lines.id"), nullable=False),
        sa.Column("picking_task_id", sa.BigInteger(), sa.ForeignKey("wes_biz.picking_tasks.id"), nullable=False),
        sa.Column(
            "member_id",
            sa.BigInteger(),
            sa.ForeignKey("wes_biz.direct_pick_executions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("completed_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id")),
        schema="wes_biz",
    )
    op.alter_column("automatic_picking_pickups", "work_id", nullable=True, schema="wes_biz")
    op.add_column("automatic_picking_pickups", sa.Column("direct_pick_id", sa.BigInteger()), schema="wes_biz")
    op.create_foreign_key(
        None,
        "automatic_picking_pickups",
        "automatic_picking_direct_picks",
        ["direct_pick_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(None, "automatic_picking_pickups", ["direct_pick_id"], schema="wes_biz")
    op.create_check_constraint(
        "automatic_pickup_source_owner",
        "automatic_picking_pickups",
        "(work_id IS NULL) <> (direct_pick_id IS NULL)",
        schema="wes_biz",
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_direct_picks)")).scalar():
        raise RuntimeError("downgrade refused: preserve DirectPick current/completion facts")
    op.drop_constraint(
        "ck_automatic_picking_pickups_automatic_pickup_source_owner", "automatic_picking_pickups", schema="wes_biz"
    )
    op.drop_constraint("uq_automatic_picking_pickups_direct_pick_id", "automatic_picking_pickups", schema="wes_biz")
    op.drop_constraint(
        op.f("fk_automatic_picking_pickups_direct_pick_id_automatic_picking_direct_picks"),
        "automatic_picking_pickups",
        schema="wes_biz",
    )
    op.drop_column("automatic_picking_pickups", "direct_pick_id", schema="wes_biz")
    op.alter_column("automatic_picking_pickups", "work_id", nullable=False, schema="wes_biz")
    op.drop_table("automatic_picking_direct_picks", schema="wes_biz")
