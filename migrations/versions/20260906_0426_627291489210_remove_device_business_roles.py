"""remove_device_business_roles

Revision ID: 627291489210
Revises: b42147d0d086
Create Date: 2026-09-06 04:26:19.367947+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "627291489210"
down_revision: Union[str, Sequence[str], None] = "b42147d0d086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """业务角色由插件配置和 Epoch binding 拥有。"""
    op.drop_column("devices", "role_index", schema="wes_biz")
    op.drop_column("devices", "device_role", schema="wes_biz")


def downgrade() -> None:
    """恢复空表结构；不重建旧业务角色或转换历史数据。"""
    op.add_column("devices", sa.Column("device_role", sa.String(length=50), nullable=False), schema="wes_biz")
    op.add_column("devices", sa.Column("role_index", sa.Integer(), nullable=False), schema="wes_biz")
