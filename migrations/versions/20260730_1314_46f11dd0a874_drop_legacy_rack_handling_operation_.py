"""drop legacy rack handling operation tables

Revision ID: 46f11dd0a874
Revises: f557c7b749b1
Create Date: 2026-07-30 13:14:42.768860+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "46f11dd0a874"
down_revision: Union[str, Sequence[str], None] = "f557c7b749b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除已由 typed WMS EFFECT 取代的通用 Rack / Handling 聚合表。"""

    op.drop_table("handling_operation_steps", schema="wes_biz")
    op.drop_table("handling_operation_moves", schema="wes_biz")
    op.drop_table("handling_operations", schema="wes_biz")
    op.drop_table("rack_tasks", schema="wes_biz")
    op.drop_table("rack_operations", schema="wes_biz")


def downgrade() -> None:
    """旧聚合无兼容或数据恢复合同。"""

    raise RuntimeError("legacy Rack / Handling operation tables are permanently retired")
