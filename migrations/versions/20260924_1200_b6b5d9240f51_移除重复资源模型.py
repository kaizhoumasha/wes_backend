"""移除 WES 重复资源主账和位置表。

Revision ID: b6b5d9240f51
Revises: c41df10527aa
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "b6b5d9240f51"
down_revision: Union[str, Sequence[str], None] = "c41df10527aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # material_mounts 是唯一引用另一张 resource 表的旧表，先删它。
    for table in (
        "resource_bin_material_mounts",
        "resource_bin_cell_occupancies",
        "resource_bin_content_snapshot_items",
        "resource_bin_content_snapshots",
        "resource_bin_placements",
        "resource_rack_bin_mounts",
        "resource_rack_placements",
        "resource_state_events",
        "resource_bins",
        "resource_bin_slot_templates",
        "resource_bin_types",
        "resource_racks",
        "resource_rack_slot_templates",
        "resource_rack_types",
    ):
        op.drop_table(table, schema="wes_biz")


def downgrade() -> None:
    raise RuntimeError("resource 主账已退役；旧数据和旧结构不提供回滚，请重建开发库")
