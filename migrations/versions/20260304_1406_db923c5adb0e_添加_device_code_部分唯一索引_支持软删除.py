"""添加 device_code 部分唯一索引（支持软删除）

Revision ID: db923c5adb0e
Revises: d967fe93d0f3
Create Date: 2026-03-04 14:06:40.332888+08:00

说明：
- 将全局唯一约束 uq_devices_device_code 替换为部分唯一索引 ux_devices_device_code_deleted
- 部分唯一索引只对未删除的记录生效（WHERE NOT is_deleted）
- 支持软删除后重用 device_code

注意：
- 不创建外键约束（device_commands.device_id 和 device_event_logs.device_id）
- PostgreSQL 外键只能引用全局唯一约束，不能引用部分唯一索引
- 数据完整性由应用层保证（Repository 验证）
"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "db923c5adb0e"
down_revision: Union[str, Sequence[str], None] = "d967fe93d0f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 删除旧的全局唯一索引（实际上是约束）
    op.drop_index(op.f("uq_devices_device_code"), table_name="devices", schema="wes_biz")

    # 创建部分唯一索引（只对未删除的记录生效）
    op.create_index(
        "ux_devices_device_code_deleted",
        "devices",
        ["device_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where="NOT is_deleted",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 删除部分唯一索引
    op.drop_index(
        "ux_devices_device_code_deleted",
        table_name="devices",
        schema="wes_biz",
        postgresql_where="NOT is_deleted",
    )

    # 恢复全局唯一索引
    op.create_index(
        op.f("uq_devices_device_code"),
        "devices",
        ["device_code"],
        unique=True,
        schema="wes_biz",
    )
