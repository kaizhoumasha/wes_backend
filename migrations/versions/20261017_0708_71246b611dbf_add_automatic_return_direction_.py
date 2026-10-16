"""保存自动回程方向命令及候选资格依据。

Revision ID: 71246b611dbf
Revises: d028d7c65eb7
"""

import sqlalchemy as sa
from alembic import op

revision = "71246b611dbf"
down_revision = "d028d7c65eb7"
branch_labels = None
depends_on = None


def upgrade():
    for column, target, target_column, kind in (
        ("scan3_command_code", "device_commands", "command_code", sa.String(80)),
        ("scan4_command_code", "device_commands", "command_code", sa.String(80)),
        ("ready_evidence_id", "inbound_evidences", "id", sa.BigInteger()),
    ):
        op.add_column("automatic_picking_returns", sa.Column(column, kind, nullable=True), schema="wes_biz")
        op.create_foreign_key(
            op.f(f"fk_automatic_picking_returns_{column}_{target}"),
            "automatic_picking_returns",
            target,
            [column],
            [target_column],
            source_schema="wes_biz",
            referent_schema="wes_biz",
        )
        op.create_unique_constraint(
            op.f(f"uq_automatic_picking_returns_{column}"),
            "automatic_picking_returns",
            [column],
            schema="wes_biz",
        )
    op.create_check_constraint(
        "automatic_return_ready_basis",
        "automatic_picking_returns",
        "ready_evidence_id IS NULL OR (scan4_evidence_id IS NOT NULL AND scan4_command_code IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_returns WHERE "
                "scan3_command_code IS NOT NULL OR scan4_command_code IS NOT NULL OR ready_evidence_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard current direction obligations or READY facts")
    op.drop_constraint("automatic_return_ready_basis", "automatic_picking_returns", schema="wes_biz", type_="check")
    for column, target in (
        ("ready_evidence_id", "inbound_evidences"),
        ("scan4_command_code", "device_commands"),
        ("scan3_command_code", "device_commands"),
    ):
        op.drop_constraint(
            op.f(f"uq_automatic_picking_returns_{column}"),
            "automatic_picking_returns",
            schema="wes_biz",
            type_="unique",
        )
        op.drop_constraint(
            op.f(f"fk_automatic_picking_returns_{column}_{target}"),
            "automatic_picking_returns",
            schema="wes_biz",
            type_="foreignkey",
        )
        op.drop_column("automatic_picking_returns", column, schema="wes_biz")
