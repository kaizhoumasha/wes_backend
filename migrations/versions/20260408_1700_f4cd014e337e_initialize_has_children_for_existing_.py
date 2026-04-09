"""initialize_has_children_for_existing_data

Revision ID: f4cd014e337e
Revises: ee46b1b4e252
Create Date: 2026-04-08 17:00:18.684553+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4cd014e337e"
down_revision: Union[str, Sequence[str], None] = "ee46b1b4e252"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """初始化 menus 和 permissions 表的 has_children 字段。

    根据现有数据计算每个节点是否有子节点（排除已软删除的子节点）。
    """
    # menus 表：更新有未删除子节点的父节点
    op.execute(
        """
        UPDATE wes_sys.menus AS parent
        SET has_children = true
        WHERE EXISTS (
            SELECT 1 FROM wes_sys.menus AS child
            WHERE child.parent_id = parent.id
            AND child.is_deleted = false
        )
        """
    )

    # permissions 表：更新有未删除子节点的父节点
    op.execute(
        """
        UPDATE wes_sys.permissions AS parent
        SET has_children = true
        WHERE EXISTS (
            SELECT 1 FROM wes_sys.permissions AS child
            WHERE child.parent_id = parent.id
            AND child.is_deleted = false
        )
        """
    )


def downgrade() -> None:
    """回滚：将所有 has_children 重置为 false（依赖 Hook 系统重新维护）。"""
    op.execute("UPDATE wes_sys.menus SET has_children = false")
    op.execute("UPDATE wes_sys.permissions SET has_children = false")
