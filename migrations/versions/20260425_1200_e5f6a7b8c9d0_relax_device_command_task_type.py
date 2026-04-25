"""relax device command task type constraint

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-25 12:00:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 插件会定义自己的设备任务类型；中心表只约束生命周期字段，不约束插件命令词表。
    op.execute("""
        ALTER TABLE wes_biz.device_commands
        DROP CONSTRAINT IF EXISTS ck_device_commands_tasktype
    """)
    op.execute("""
        ALTER TABLE wes_biz.device_commands
        DROP CONSTRAINT IF EXISTS tasktype
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        ALTER TABLE wes_biz.device_commands
        DROP CONSTRAINT IF EXISTS ck_device_commands_tasktype
    """)
    op.execute("""
        ALTER TABLE wes_biz.device_commands
        DROP CONSTRAINT IF EXISTS tasktype
    """)
    op.execute("""
        ALTER TABLE wes_biz.device_commands
        ADD CONSTRAINT ck_device_commands_tasktype
        CHECK (task_type IN (
            'PICK', 'PUT', 'SCAN', 'ROTATE', 'PROCESS',
            'PICK_AND_PLACE', 'PICK_AND_PUT',
            'MOVE_FORWARD', 'MOVE_BACKWARD', 'STOP',
            'OUTPUT', 'PICK_NG'
        ))
    """)
