"""闭合粗分持久触发

Revision ID: ec18b2a79400
Revises: 5695afa99545
Create Date: 2026-08-18 01:31:14.548432+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec18b2a79400"
down_revision: Union[str, Sequence[str], None] = "5695afa99545"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为活动 Epoch 冻结粗分逻辑位置拓扑；不迁移开发数据。"""

    op.create_table(
        "line_run_epoch_position_bindings",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("line_run_epoch_id", sa.BigInteger(), nullable=False),
        sa.Column("position_role", sa.String(length=50), nullable=False),
        sa.Column("location_id", sa.String(length=120), nullable=False),
        sa.Column("location_type", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "line_run_epoch_id",
            "position_role",
            name="ux_line_run_epoch_position_bindings_epoch_role",
        ),
        sa.UniqueConstraint(
            "line_run_epoch_id",
            "location_id",
            name="ux_line_run_epoch_position_bindings_epoch_location",
        ),
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_line_run_epoch_position_bindings_id"),
        "line_run_epoch_position_bindings",
        ["id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_line_run_epoch_position_bindings_line_run_epoch_id"),
        "line_run_epoch_position_bindings",
        ["line_run_epoch_id"],
        schema="wes_biz",
    )
    op.add_column(
        "rack_replacement_transport_bindings",
        sa.Column("line_run_epoch_id", sa.BigInteger(), nullable=False),
        schema="wes_biz",
    )
    op.add_column(
        "rack_replacement_transport_bindings",
        sa.Column("current_rack_id", sa.String(length=80), nullable=False),
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_rack_replacement_transport_bindings_epoch",
        "rack_replacement_transport_bindings",
        "line_run_epochs",
        ["line_run_epoch_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(
        "ux_rack_replacement_transport_bindings_epoch_rack_leg",
        "rack_replacement_transport_bindings",
        ["line_run_epoch_id", "current_rack_id", "leg"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_rack_replacement_transport_bindings_epoch_rack",
        "rack_replacement_transport_bindings",
        ["line_run_epoch_id", "current_rack_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Phase 8 direct cutover 不回退已投入使用的 Epoch 拓扑。"""
