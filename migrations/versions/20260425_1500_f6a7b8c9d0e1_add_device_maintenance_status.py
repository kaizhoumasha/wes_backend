"""add device maintenance status

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-25 15:00:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        ALTER TABLE wes_biz.devices
        DROP CONSTRAINT IF EXISTS devicestatus
    """)
    op.execute("""
        ALTER TABLE wes_biz.devices
        DROP CONSTRAINT IF EXISTS ck_devices_device_status
    """)
    op.execute("""
        ALTER TABLE wes_biz.devices
        ADD CONSTRAINT devicestatus
        CHECK (device_status IN ('IDLE', 'RUNNING', 'ERROR', 'OFFLINE', 'MAINTENANCE'))
    """)
    op.execute("""
        UPDATE wes_biz.devices
        SET device_status = 'MAINTENANCE',
            current_command_id = NULL,
            error_code = COALESCE(NULLIF(error_code, ''), 'MAINTENANCE')
        WHERE maintenance_mode = true
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # 旧模型没有 MAINTENANCE 状态，降级时保留人工维护语义到 maintenance_mode。
    op.execute("""
        UPDATE wes_biz.devices
        SET maintenance_mode = true,
            device_status = 'IDLE'
        WHERE device_status = 'MAINTENANCE'
    """)
    op.execute("""
        ALTER TABLE wes_biz.devices
        DROP CONSTRAINT IF EXISTS devicestatus
    """)
    op.execute("""
        ALTER TABLE wes_biz.devices
        DROP CONSTRAINT IF EXISTS ck_devices_device_status
    """)
    op.execute("""
        ALTER TABLE wes_biz.devices
        ADD CONSTRAINT devicestatus
        CHECK (device_status IN ('IDLE', 'RUNNING', 'ERROR', 'OFFLINE'))
    """)
