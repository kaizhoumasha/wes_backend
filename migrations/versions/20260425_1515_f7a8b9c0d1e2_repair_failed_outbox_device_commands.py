"""repair failed outbox device commands

Revision ID: f7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-25 15:15:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 历史版本可能出现 Outbox 已永久失败但 DeviceCommand 仍处于 active 状态的脏数据。
    # 这会让设备占用投影持续认为有未闭环任务，阻塞后续正确派发。
    op.execute("""
        UPDATE wes_biz.device_commands AS dc
        SET status = 'FAILED',
            completed_at = COALESCE(dc.completed_at, now() AT TIME ZONE 'UTC'),
            error_detail = json_build_object(
                'message', COALESCE(wo.last_error, 'Outbox dispatch failed'),
                'source', 'OUTBOX_DISPATCH_REPAIR'
            )
        FROM wes_biz.workline_outbox AS wo
        WHERE wo.dispatch_type = 'DEVICE_COMMAND'
          AND wo.status = 'FAILED'
          AND wo.payload_json ->> 'command_code' = dc.command_code
          AND dc.status IN ('PENDING', 'SENT', 'ACK_RECEIVED')
    """)
    op.execute("""
        UPDATE wes_biz.devices AS d
        SET device_status = 'IDLE',
            current_command_id = NULL,
            error_code = NULL
        FROM wes_biz.device_commands AS dc
        WHERE d.current_command_id = dc.id
          AND dc.status = 'FAILED'
          AND d.device_status = 'RUNNING'
          AND NOT EXISTS (
              SELECT 1
              FROM wes_biz.device_commands AS active_dc
              WHERE active_dc.device_id = d.id
                AND active_dc.id <> dc.id
                AND active_dc.status IN ('SENT', 'ACK_RECEIVED')
          )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # 数据修复不可逆，降级时保留修复后的闭环状态。
