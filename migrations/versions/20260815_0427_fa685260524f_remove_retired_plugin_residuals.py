"""删除退役插件活动残留

Revision ID: fa685260524f
Revises: ce53af214081
Create Date: 2026-08-15 04:27:16.388269+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa685260524f"
down_revision: Union[str, Sequence[str], None] = "ce53af214081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除工作线诊断中的退役插件身份。"""

    op.drop_column("workline_diagnostics", "plugin_key", schema="wes_biz")


def downgrade() -> None:
    """未发布系统不恢复退役插件身份。"""

    raise NotImplementedError("不支持恢复退役插件诊断身份")
