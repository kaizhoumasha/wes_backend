"""add material_units reel root entity

Revision ID: e680301d30c8
Revises: fb02178f9772
Create Date: 2026-06-21 10:18:01.805419+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e680301d30c8"
down_revision: Union[str, Sequence[str], None] = "fb02178f9772"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _data_columns() -> list[sa.Column]:
    """DataTableMixin 标准列：created_at + updated_at + id。"""
    return [
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间 (UTC)",
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "material_units",
        *_data_columns(),
        sa.Column("pkg_code", sa.String(200), nullable=False, comment="PkgID，单盘物理唯一业务键"),
        sa.Column("material_identity_key", sa.String(300), nullable=False, comment="物料属性键"),
        sa.Column("six_in_one", sa.JSON(), nullable=True, comment="六合一码全字段"),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="IN_TRANSIT",
            comment="料盘状态",
        ),
        sa.Column("current_location", sa.String(200), nullable=True, comment="当前格位/工位"),
        sa.Column("current_session_id", sa.BigInteger(), nullable=True, comment="当前处理 Session ID"),
        sa.Column("reconciliation_from_state", sa.String(50), nullable=True, comment="对账前 status"),
        sa.CheckConstraint(
            "status IN ('IN_TRANSIT', 'STORED', 'COMPLETED', 'NG', 'RECONCILING')",
            name="ck_material_units_status",
        ),
        schema="wes_biz",
    )
    op.create_index("ix_material_units_pkg_code", "material_units", ["pkg_code"], unique=True, schema="wes_biz")
    op.create_index("ix_material_units_status", "material_units", ["status"], schema="wes_biz")
    op.create_index(
        "ix_material_units_current_session_id",
        "material_units",
        ["current_session_id"],
        schema="wes_biz",
    )

    # workline_sessions 加 current_material_unit_id 列
    op.add_column(
        "workline_sessions",
        sa.Column("current_material_unit_id", sa.BigInteger(), nullable=True, comment="当前料盘 material_unit ID"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_workline_sessions_current_material_unit_id",
        "workline_sessions",
        ["current_material_unit_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_workline_sessions_current_material_unit_id", table_name="workline_sessions", schema="wes_biz")
    op.drop_column("workline_sessions", "current_material_unit_id", schema="wes_biz")

    op.drop_index("ix_material_units_current_session_id", table_name="material_units", schema="wes_biz")
    op.drop_index("ix_material_units_status", table_name="material_units", schema="wes_biz")
    op.drop_index("ix_material_units_pkg_code", table_name="material_units", schema="wes_biz")
    op.drop_table("material_units", schema="wes_biz")
