"""修改 device 关联为物理主键 id

Revision ID: 71fdc7253cde
Revises: db923c5adb0e
Create Date: 2026-03-04 14:13:29.196419+08:00

说明：
- 将 device_commands.device_id 和 device_event_logs.device_id 从 VARCHAR（存储 device_code）
  改为 INTEGER（存储 Device.id）
- 将 devices.current_command_id 从 VARCHAR（存储 command_id）改为 INTEGER
- 添加外键约束确保引用完整性

数据迁移：
- 使用临时列进行数据转换，避免 PostgreSQL ALTER TABLE ... USING 的子查询限制
"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "71fdc7253cde"
down_revision: Union[str, Sequence[str], None] = "db923c5adb0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # ===== 1. 修改 device_commands.device_id =====
    # 步骤 1: 添加临时列
    op.add_column(
        "device_commands",
        sa.Column("device_id_new", sa.Integer(), nullable=True),
        schema="wes_biz",
    )

    # 步骤 2: 填充数据（通过 device_code 查找对应的 Device.id）
    op.execute("""
        UPDATE wes_biz.device_commands dc
        SET device_id_new = d.id
        FROM wes_biz.devices d
        WHERE d.device_code = dc.device_id
    """)

    # 步骤 3: 删除旧列，重命名新列
    op.drop_column("device_commands", "device_id", schema="wes_biz")
    op.alter_column(
        "device_commands",
        "device_id_new",
        new_column_name="device_id",
        nullable=False,
        schema="wes_biz",
    )

    # 步骤 4: 添加外键约束
    op.create_foreign_key(
        "fk_device_commands_device_id",
        "device_commands", "devices",
        ["device_id"], ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )

    # ===== 2. 修改 device_event_logs.device_id =====
    op.add_column(
        "device_event_logs",
        sa.Column("device_id_new", sa.Integer(), nullable=True),
        schema="wes_biz",
    )

    op.execute("""
        UPDATE wes_biz.device_event_logs del
        SET device_id_new = d.id
        FROM wes_biz.devices d
        WHERE d.device_code = del.device_id
    """)

    op.drop_column("device_event_logs", "device_id", schema="wes_biz")
    op.alter_column(
        "device_event_logs",
        "device_id_new",
        new_column_name="device_id",
        nullable=False,
        schema="wes_biz",
    )

    op.create_foreign_key(
        "fk_device_event_logs_device_id",
        "device_event_logs", "devices",
        ["device_id"], ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )

    # ===== 3. 修改 devices.current_command_id =====
    # 当前存储的是 command_id（字符串），需要转换为对应的 DeviceCommand.id
    op.add_column(
        "devices",
        sa.Column("current_command_id_new", sa.Integer(), nullable=True),
        schema="wes_biz",
    )

    op.execute("""
        UPDATE wes_biz.devices d
        SET current_command_id_new = dc.id
        FROM wes_biz.device_commands dc
        WHERE dc.command_id = d.current_command_id
    """)

    op.drop_column("devices", "current_command_id", schema="wes_biz")
    op.alter_column(
        "devices",
        "current_command_id_new",
        new_column_name="current_command_id",
        nullable=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""

    # ===== 1. 回退 devices.current_command_id =====
    op.add_column(
        "devices",
        sa.Column("current_command_id_new", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )

    op.execute("""
        UPDATE wes_biz.devices d
        SET current_command_id_new = dc.command_id
        FROM wes_biz.device_commands dc
        WHERE dc.id = d.current_command_id
    """)

    op.drop_column("devices", "current_command_id", schema="wes_biz")
    op.alter_column(
        "devices",
        "current_command_id_new",
        new_column_name="current_command_id",
        nullable=True,
        schema="wes_biz",
    )

    # ===== 2. 回退 device_event_logs.device_id =====
    op.drop_constraint(
        "fk_device_event_logs_device_id",
        "device_event_logs",
        schema="wes_biz",
        type_="foreignkey",
    )

    op.add_column(
        "device_event_logs",
        sa.Column("device_id_new", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )

    op.execute("""
        UPDATE wes_biz.device_event_logs del
        SET device_id_new = d.device_code
        FROM wes_biz.devices d
        WHERE d.id = del.device_id
    """)

    op.drop_column("device_event_logs", "device_id", schema="wes_biz")
    op.alter_column(
        "device_event_logs",
        "device_id_new",
        new_column_name="device_id",
        nullable=False,
        schema="wes_biz",
    )

    # ===== 3. 回退 device_commands.device_id =====
    op.drop_constraint(
        "fk_device_commands_device_id",
        "device_commands",
        schema="wes_biz",
        type_="foreignkey",
    )

    op.add_column(
        "device_commands",
        sa.Column("device_id_new", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )

    op.execute("""
        UPDATE wes_biz.device_commands dc
        SET device_id_new = d.device_code
        FROM wes_biz.devices d
        WHERE d.id = dc.device_id
    """)

    op.drop_column("device_commands", "device_id", schema="wes_biz")
    op.alter_column(
        "device_commands",
        "device_id_new",
        new_column_name="device_id",
        nullable=False,
        schema="wes_biz",
    )
