"""drop demo module tables

Revision ID: 4d08cdff2766
Revises: a6c2c77adabd
Create Date: 2026-05-28 14:32:38.432069+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d08cdff2766"
down_revision: Union[str, Sequence[str], None] = "a6c2c77adabd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(
        op.f("ix_wes_biz_demo_product_lists_id"), table_name="demo_product_lists", schema=SCHEMA, if_exists=True
    )
    op.drop_table("demo_product_lists", schema=SCHEMA, if_exists=True)
    op.drop_index(op.f("ix_wes_biz_demo_products_id"), table_name="demo_products", schema=SCHEMA, if_exists=True)
    op.drop_index("demo_products_name_active_unique", table_name="demo_products", schema=SCHEMA, if_exists=True)
    op.drop_table("demo_products", schema=SCHEMA, if_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "demo_products",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("deleted_by", sa.Integer(), nullable=True, comment="删除人ID"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("is_deleted", sa.Boolean(), server_default="FALSE", nullable=False, comment="是否已删除"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="产品名称"),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_wes_biz_demo_products_id"),
        "demo_products",
        ["id"],
        unique=True,
        schema=SCHEMA,
        if_not_exists=True,
    )
    op.create_index(
        "demo_products_name_active_unique",
        "demo_products",
        ["name"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("NOT is_deleted"),
        if_not_exists=True,
    )
    op.create_table(
        "demo_product_lists",
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], [f"{SCHEMA}.demo_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_wes_biz_demo_product_lists_id"),
        "demo_product_lists",
        ["id"],
        unique=True,
        schema=SCHEMA,
        if_not_exists=True,
    )
