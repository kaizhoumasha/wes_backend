"""govern device runtime state

Revision ID: a1b2c3d4e5f7
Revises: 9b7c6d5e4f3a
Create Date: 2026-05-07 10:15:00.000000+08:00

设备运行态收敛：
- 运行态字段只能组成合法投影。
- 单设备固定单硬件任务。
- 心跳扫描增加部分索引。
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "9b7c6d5e4f3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Repair existing device runtime projections and add guard constraints."""

    op.execute("UPDATE wes_biz.devices SET max_concurrent_tasks = 1 WHERE max_concurrent_tasks <> 1")
    op.execute(
        """
        UPDATE wes_biz.devices
        SET device_status = 'MAINTENANCE',
            maintenance_mode = true,
            current_command_id = NULL,
            error_code = COALESCE(NULLIF(error_code, ''), 'MAINTENANCE')
        WHERE maintenance_mode = true
        """
    )
    op.execute(
        """
        UPDATE wes_biz.devices
        SET device_status = 'IDLE',
            current_command_id = NULL,
            error_code = NULL,
            maintenance_mode = false
        WHERE device_status = 'RUNNING'
          AND current_command_id IS NULL
          AND maintenance_mode = false
        """
    )
    op.execute(
        """
        UPDATE wes_biz.devices
        SET current_command_id = NULL,
            error_code = NULL,
            maintenance_mode = false
        WHERE device_status = 'IDLE'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.devices
        SET current_command_id = NULL,
            error_code = COALESCE(NULLIF(error_code, ''), 'UNKNOWN_DEVICE_ERROR'),
            maintenance_mode = false
        WHERE device_status = 'ERROR'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.devices
        SET current_command_id = NULL,
            error_code = COALESCE(NULLIF(error_code, ''), 'HEARTBEAT_TIMEOUT'),
            maintenance_mode = false
        WHERE device_status = 'OFFLINE'
        """
    )

    op.execute(
        """
        ALTER TABLE wes_biz.devices
        ADD CONSTRAINT ck_devices_single_hardware_task
        CHECK (max_concurrent_tasks = 1)
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.devices
        ADD CONSTRAINT ck_devices_runtime_projection
        CHECK (
            (
                device_status = 'IDLE'
                AND current_command_id IS NULL
                AND error_code IS NULL
                AND maintenance_mode = false
            )
            OR (
                device_status = 'RUNNING'
                AND current_command_id IS NOT NULL
                AND error_code IS NULL
                AND maintenance_mode = false
            )
            OR (
                device_status = 'ERROR'
                AND current_command_id IS NULL
                AND error_code IS NOT NULL
                AND maintenance_mode = false
            )
            OR (
                device_status = 'OFFLINE'
                AND current_command_id IS NULL
                AND error_code IS NOT NULL
                AND maintenance_mode = false
            )
            OR (
                device_status = 'MAINTENANCE'
                AND current_command_id IS NULL
                AND error_code IS NOT NULL
                AND maintenance_mode = true
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_devices_heartbeat_scan_runtime
        ON wes_biz.devices (last_heartbeat_at, device_status)
        WHERE is_deleted = false
          AND maintenance_mode = false
          AND last_heartbeat_at IS NOT NULL
          AND device_status IN ('IDLE', 'RUNNING')
        """
    )


def downgrade() -> None:
    """Remove runtime governance database guards."""

    op.execute("DROP INDEX IF EXISTS wes_biz.ix_devices_heartbeat_scan_runtime")
    op.execute("ALTER TABLE wes_biz.devices DROP CONSTRAINT IF EXISTS ck_devices_runtime_projection")
    op.execute("ALTER TABLE wes_biz.devices DROP CONSTRAINT IF EXISTS ck_devices_single_hardware_task")
