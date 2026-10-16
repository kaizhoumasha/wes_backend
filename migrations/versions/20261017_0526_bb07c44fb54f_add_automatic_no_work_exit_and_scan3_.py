"""保存自动 NO_WORK 点2命令和首次 SCAN3 交接。

Revision ID: bb07c44fb54f
Revises: 55adfc1a25a7
"""

import sqlalchemy as sa
from alembic import op

revision = "bb07c44fb54f"
down_revision = "55adfc1a25a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "automatic_picking_works", sa.Column("exit_command_code", sa.String(80), nullable=True), schema="wes_biz"
    )
    op.create_foreign_key(
        op.f("fk_automatic_picking_works_exit_command_code_device_commands"),
        "automatic_picking_works",
        "device_commands",
        ["exit_command_code"],
        ["command_code"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(
        op.f("uq_automatic_picking_works_exit_command_code"),
        "automatic_picking_works",
        ["exit_command_code"],
        schema="wes_biz",
    )
    op.create_table(
        "automatic_picking_returns",
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
        sa.Column("bin_code", sa.String(100), nullable=False),
        sa.Column("source_work_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("entry_command_code", sa.String(80), nullable=False),
        sa.Column("scan3_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "source_work_id > 0", name=op.f("ck_automatic_picking_returns_automatic_return_source_work")
        ),
        sa.ForeignKeyConstraint(
            ["workline_id"], ["wes_biz.work_lines.id"], name=op.f("fk_automatic_picking_returns_workline_id_work_lines")
        ),
        sa.ForeignKeyConstraint(
            ["entry_command_code"],
            ["wes_biz.device_commands.command_code"],
            name=op.f("fk_automatic_picking_returns_entry_command_code_device_commands"),
        ),
        sa.ForeignKeyConstraint(
            ["scan3_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_automatic_picking_returns_scan3_evidence_id_inbound_evidences"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automatic_picking_returns")),
        sa.UniqueConstraint("source_work_id", name=op.f("uq_automatic_picking_returns_source_work_id")),
        sa.UniqueConstraint("entry_command_code", name=op.f("uq_automatic_picking_returns_entry_command_code")),
        sa.UniqueConstraint("scan3_evidence_id", name=op.f("uq_automatic_picking_returns_scan3_evidence_id")),
        schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_returns) OR EXISTS "
                "(SELECT 1 FROM wes_biz.automatic_picking_works WHERE exit_command_code IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic exit or SCAN3 handoff identities")
    op.drop_table("automatic_picking_returns", schema="wes_biz")
    op.drop_constraint(
        op.f("uq_automatic_picking_works_exit_command_code"),
        "automatic_picking_works",
        schema="wes_biz",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_automatic_picking_works_exit_command_code_device_commands"),
        "automatic_picking_works",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("automatic_picking_works", "exit_command_code", schema="wes_biz")
