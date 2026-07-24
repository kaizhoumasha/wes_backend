"""删除字符串式 Port 方法快照

Revision ID: 5d251fdbb1e8
Revises: 7824db01402d
Create Date: 2026-07-23 14:45:22.335487+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d251fdbb1e8"
down_revision: Union[str, Sequence[str], None] = "7824db01402d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除 binding 与 WorkLine 上已停用的字符串式方法清单。"""

    op.drop_column("workline_plugin_bindings", "port_requirements_json", schema="wes_biz")
    op.drop_column("work_lines", "active_plugin_port_requirements_json", schema="wes_biz")


def downgrade() -> None:
    """恢复旧字段结构，不恢复任何历史数据。"""

    empty_json = sa.text("'[]'::json")
    op.add_column(
        "work_lines",
        sa.Column("active_plugin_port_requirements_json", sa.JSON(), nullable=False, server_default=empty_json),
        schema="wes_biz",
    )
    op.add_column(
        "workline_plugin_bindings",
        sa.Column("port_requirements_json", sa.JSON(), nullable=False, server_default=empty_json),
        schema="wes_biz",
    )
    op.alter_column(
        "workline_plugin_bindings",
        "port_requirements_json",
        server_default=None,
        schema="wes_biz",
    )
