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

    async def get_runtime_correlation_id(
        self,
        db: AsyncSession,
        *,
        command_code: str,
        command_id: int | None,
    ) -> str | None:
        """读取命令创建时固定的 runtime correlation，不暴露设备 ORM。"""

        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(columns.correlation_id).where(columns.command_code == command_code)
        if command_id is not None:
            statement = statement.where(columns.id == command_id)
        value = await db.scalar(statement.limit(1))
        return str(value) if value is not None else None

    @staticmethod
    def build_runtime_correlation_statement(
        *,
        correlation_id: str,
        awaiting_command_code: str,
        workline_id: int,
        task_type: str,
        handoff_demand_id: int,
        handoff_source_item_id: int,
        claim_attempt_no: int,
        source_pick_inbox_id: int,
        source_pick_request_event_id: str,
        limit: int = 2,
    ) -> Any:
        """以 correlation 索引为入口，先过滤完整恢复证据，再读取至多两行。"""

        columns = cast("Any", DeviceCommand).__table__.c
        return (
            select(DeviceCommand)
            .where(
                columns.correlation_id == correlation_id,
                columns.command_code == awaiting_command_code,
                columns.workline_id == workline_id,
                columns.task_type == task_type,
                columns.params["handoff_demand_id"].as_integer() == handoff_demand_id,
                columns.params["handoff_source_item_id"].as_integer() == handoff_source_item_id,
                columns.params["claim_attempt_no"].as_integer() == claim_attempt_no,
                columns.params["source_pick_inbox_id"].as_integer() == source_pick_inbox_id,
                columns.params["source_pick_request_event_id"].as_string() == source_pick_request_event_id,
            )
            .order_by(columns.id.asc())
            .limit(min(max(limit, 1), 2))
        )

    async def list_by_runtime_correlation(
        self,
        db: AsyncSession,
        *,
        correlation_id: str,
        awaiting_command_code: str,
        workline_id: int,
        task_type: str,
        handoff_demand_id: int,
        handoff_source_item_id: int,
        claim_attempt_no: int,
        source_pick_inbox_id: int,
        source_pick_request_event_id: str,
        limit: int = 2,
    ) -> list[DeviceCommand]:
        """按已索引 correlation 与完整恢复证据读取至多两个候选。"""

        result = await db.execute(
            self.build_runtime_correlation_statement(
                correlation_id=correlation_id,
                awaiting_command_code=awaiting_command_code,
                workline_id=workline_id,
                task_type=task_type,
                handoff_demand_id=handoff_demand_id,
                handoff_source_item_id=handoff_source_item_id,
                claim_attempt_no=claim_attempt_no,
                source_pick_inbox_id=source_pick_inbox_id,
                source_pick_request_event_id=source_pick_request_event_id,
                limit=limit,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def add_runtime_effect(
        db: AsyncSession,
        command: DeviceCommand,
        intent_log: object,
        outbox: object,
    ) -> None:
        """在调用方外层事务中持久化命令与 EFFECT 双账本，只 flush。"""

        db.add(command)
        await db.flush()
        from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
            runtime_intent_log_repository,
        )

        await runtime_intent_log_repository.add_proposed_pair(
            db,
            intent_log=intent_log,  # type: ignore[arg-type]
            outbox=outbox,  # type: ignore[arg-type]
        )

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

    async def get_unfinished_commands_for_device(
        self,
        db: AsyncSession,
        device_id: int,
        *,
        limit: int = 1,
    ) -> list[DeviceCommand]:
        """获取设备尚未闭环的指令，用于新指令的原子准入检查。

        与 ``get_active_commands_for_device`` 不同，这里必须包含 PENDING：
        PENDING 虽不代表硬件已进入 RUNNING，但已占用该设备的命令槽位。
        """

        unfinished_statuses = [CommandStatus.PENDING, CommandStatus.SENT, CommandStatus.ACK_RECEIVED]
        columns = cast("Any", DeviceCommand).__table__.c
        statement = (
            select(DeviceCommand)
            .where(
                columns.device_id == device_id,
                columns.status.in_(unfinished_statuses),
            )
            .order_by(columns.created_at.desc(), columns.id.desc())
            .limit(limit)
        )
        result = await db.execute(statement)
        return list(result.scalars().all())

    async def list_unfinished_for_workline_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        trace_id: str,
    ) -> list[DeviceCommand]:
        """批量锁定工作线未闭环指令，供跨域阶段门在同一事务内校验。"""

        if not trace_id.strip():
            raise ValueError("unfinished command query requires trace_id")
        unfinished_statuses = [CommandStatus.PENDING, CommandStatus.SENT, CommandStatus.ACK_RECEIVED]
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.workline_id == workline_id,
                columns.trace_id == trace_id,
                columns.status.in_(unfinished_statuses),
            )
            .order_by(columns.id)
            .with_for_update()
        )
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
