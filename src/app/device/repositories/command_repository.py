"""
设备指令 Repository (Device Command Repository)

提供设备指令的数据访问操作。
"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


class DeviceCommandRepository(BaseRepository[DeviceCommand]):
    """设备指令 Repository"""

    def __init__(self):
        """初始化 Repository"""
        super().__init__(DeviceCommand)

    async def get_by_command_code(self, db: AsyncSession, command_code: str) -> DeviceCommand | None:
        """
        根据 command_code 查询指令

        Args:
            db: 数据库会话
            command_code: 指令编码

        Returns:
            DeviceCommand 实例，如果不存在返回 None
        """
        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(DeviceCommand).where(columns.command_code == command_code)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_pending_commands(
        self,
        db: AsyncSession,
        device_id: int | None = None,
        limit: int = 100,
    ) -> list[DeviceCommand]:
        """
        获取待处理的指令

        Args:
            db: 数据库会话
            device_id: 设备 ID（可选，为空时查询所有设备）
            limit: 返回数量限制

        Returns:
            待处理指令列表
        """
        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(DeviceCommand).where(columns.status == CommandStatus.PENDING)

        if device_id is not None:
            statement = statement.where(columns.device_id == device_id)

        statement = statement.order_by(columns.priority.desc()).limit(limit)

        result = await db.execute(statement)
        return list(result.scalars().all())

    async def get_timeout_commands(self, db: AsyncSession, limit: int = 100) -> list[DeviceCommand]:
        """
        获取超时的指令

        Args:
            db: 数据库会话
            limit: 返回数量限制

        Returns:
            超时指令列表
        """
        # 查询已发送但未完成的指令
        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(DeviceCommand).where(columns.status.in_([CommandStatus.SENT, CommandStatus.ACK_RECEIVED]))

        result = await db.execute(statement)
        commands = list(result.scalars().all())

        # 过滤超时的指令
        timeout_commands = [cmd for cmd in commands if cmd.is_timeout()]

        return timeout_commands[:limit]

    async def get_ack_timed_out_commands(self, db: AsyncSession, limit: int = 100) -> list[DeviceCommand]:
        """获取已发送但一直没有收到 ACK 的超时指令。"""

        columns = cast("Any", DeviceCommand).__table__.c
        statement = (
            select(DeviceCommand)
            .where(
                columns.status == CommandStatus.SENT,
                columns.sent_at.is_not(None),
                columns.ack_received_at.is_(None),
                columns.session_id_int.is_not(None),
                columns.workline_id.is_not(None),
            )
            .order_by(columns.sent_at.asc(), columns.id.asc())
        )

        result = await db.execute(statement)
        commands = list(result.scalars().all())
        return [command for command in commands if command.is_timeout()][:limit]

    async def get_active_commands_for_device(
        self,
        db: AsyncSession,
        device_id: int,
        *,
        exclude_command_id: int | None = None,
        limit: int = 1,
    ) -> list[DeviceCommand]:
        """获取设备已进入硬件侧且仍未闭环的指令，用于推导设备占用状态。

        PENDING 只表示 WES 侧排队，不能让设备提前进入 RUNNING。
        """

        active_statuses = [CommandStatus.SENT, CommandStatus.ACK_RECEIVED]
        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(DeviceCommand).where(
            columns.device_id == device_id,
            columns.status.in_(active_statuses),
        )
        if exclude_command_id is not None:
            statement = statement.where(columns.id != exclude_command_id)

        statement = statement.order_by(columns.created_at.desc(), columns.id.desc()).limit(limit)
        result = await db.execute(statement)
        return list(result.scalars().all())

    async def get_commands_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[DeviceCommand]:
        """
        根据 Trace ID 查询所有相关指令

        Args:
            db: 数据库会话
            trace_id: Trace ID

        Returns:
            相关指令列表
        """
        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(DeviceCommand).where(columns.trace_id == trace_id).order_by(columns.created_at)

        result = await db.execute(statement)
        return list(result.scalars().all())

    async def count_by_status(self, db: AsyncSession, status: CommandStatus, device_id: int | None = None) -> int:
        """
        统计指定状态的指令数量

        Args:
            db: 数据库会话
            status: 指令状态
            device_id: 设备 ID（可选）

        Returns:
            指令数量
        """
        from sqlalchemy import func

        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(func.count(columns.id)).where(columns.status == status)

        if device_id is not None:
            statement = statement.where(columns.device_id == device_id)

        result = await db.execute(statement)
        return result.scalar_one() or 0

    async def cancel_active_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        incident_id: int,
    ) -> int:
        """取消 WorkLine 尚未闭环的设备指令。"""

        active_statuses = [CommandStatus.PENDING, CommandStatus.SENT, CommandStatus.ACK_RECEIVED]
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(active_statuses),
            )
            .with_for_update()
        )
        commands = list(result.scalars().all())
        now = timezone.now_for_db()
        for command in commands:
            command.status = CommandStatus.CANCELLED
            command.completed_at = now
            command.error_detail = {
                "error_code": "CANCELLED_BY_ESTOP",
                "error_message": "WorkLine 急停冻结，指令已取消",
                "safety_incident_id": incident_id,
            }
        return len(commands)


# 创建单例
device_command_repository = DeviceCommandRepository()


__all__ = [
    "DeviceCommandRepository",
    "device_command_repository",
]
