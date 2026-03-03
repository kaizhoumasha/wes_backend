"""设备指令服务 (Device Command Service)

提供设备指令的 CRUD 操作, 专注于数据访问层。

职责划分:
- Service 层: CRUD 操作(创建, 查询, 更新, 删除)
- Celery 任务层: 业务逻辑(SDAF 流程: 验证, 决策, 构建, 发送)
"""

import uuid
from typing import Any

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import (
    CommandAck,
    CommandCallbackResult,
    CommandRequest,
    CommandStatus,
    DeviceCommand,
)
from src.app.device.models.event_log import DeviceEventLog, EventRequest
from src.app.device.repositories.command_repository import (
    DeviceCommandRepository,
    device_command_repository,
)
from src.core.base_service import BaseService
from src.core.exceptions import NotFoundException
from src.utils.timezone import timezone


class DeviceCommandService(BaseService[DeviceCommand, DeviceCommandRepository]):
    """设备指令服务（纯 CRUD 层）

    只负责数据访问操作，不包含业务逻辑。
    业务逻辑（SDAF 流程）在 Celery 任务层实现。
    """

    def __init__(self) -> None:
        """初始化服务"""
        super().__init__(
            device_command_repository,
            enable_cache=True,
            cache_prefix="app:device:command",
        )
        # HTTP 客户端配置
        self.http_timeout = 10.0  # 10 秒超时
        self.max_retries = 3

    # ==================== CRUD 操作 ====================

    async def create_command(
        self,
        db: AsyncSession,
        command_request: CommandRequest,
    ) -> DeviceCommand:
        """创建设备指令"""
        # 生成 command_id（如果未提供）
        command_id = command_request.command_id
        if not command_id:
            command_id = self._generate_command_id(
                command_request.device_id,
                command_request.task_type.value,
            )

        # 生成 correlation_id（如果未提供）
        correlation_id = command_request.correlation_id
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # 创建指令记录
        command_data = {
            "command_id": command_id,
            "device_id": command_request.device_id,
            "task_type": command_request.task_type,
            "priority": command_request.priority,
            "timeout_ms": command_request.timeout_ms,
            "params": command_request.params,
            "correlation_id": correlation_id,
            "status": CommandStatus.PENDING,
        }

        command = await self.repo.create(db, command_data)
        logger.info(f"创建指令: {command_id} -> {command_request.task_type.value}")

        return command

    async def send_command(
        self,
        db: AsyncSession,
        command_id: str,
        device_url: str | None = None,
    ) -> CommandAck:
        """
        发送指令到设备

        Args:
            db: 数据库会话
            command_id: 指令 ID
            device_url: 设备 URL（可选）

        Returns:
            设备返回的 ACK 确认
        """
        # 1. 获取指令
        command = await self.repo.get_by_command_id(db, command_id)
        if not command:
            raise NotFoundException(f"指令不存在: {command_id}")

        # 2. 获取设备 URL
        if not device_url:
            device_url = await self._get_device_url(db, command.device_id)

        # 3. 构建请求体（白皮书 3.1.1 格式）
        request_body = {
            "command_id": command.command_id,
            "task_type": command.task_type.value,
            "priority": command.priority,
            "timeout": command.timeout_ms,
            "params": command.params,
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
        }

        # 4. 发送 HTTP 请求
        url = f"{device_url}/api/v1/device/command"
        logger.info(f"发送指令到设备: {url} -> {command_id}")

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.post(url, json=request_body)
                response.raise_for_status()

                ack_data = response.json()
                ack = CommandAck(**ack_data)

                # 5. 更新指令状态
                await self._update_sent_status(
                    db, command, ack.code, ack.message, ack.trace_id
                )

                logger.info(
                    f"指令已发送: {command_id} -> ACK {ack.code} ({ack.message})"
                )
                return ack

        except httpx.HTTPStatusError as e:
            logger.error(f"发送指令失败: {command_id} -> HTTP {e.response.status_code}")
            await self._update_command_status(
                db, command, CommandStatus.FAILED, error_detail=str(e)
            )
            raise
        except httpx.RequestError as e:
            logger.error(f"发送指令失败: {command_id} -> 网络错误 {e}")
            await self._update_command_status(
                db, command, CommandStatus.TIMEOUT, error_detail=str(e)
            )
            raise

    async def handle_callback_result(
        self,
        db: AsyncSession,
        callback: CommandCallbackResult,
    ) -> DeviceCommand:
        """处理设备回调结果（更新指令状态）"""
        # 1. 获取指令
        command = await self.repo.get_by_command_id(db, callback.command_id)
        if not command:
            raise NotFoundException(f"回调指令不存在: {callback.command_id}")

        # 2. 解析完成时间（使用 UTC 时区）
        completed_at = timezone.to_utc(callback.finish_time / 1000)

        # 3. 更新指令状态
        update_data = {
            "result": callback.result,
            "completed_at": completed_at,
            "result_data": callback.data,
            "error_detail": callback.error_detail,
        }

        if callback.result == "SUCCESS":
            update_data["status"] = CommandStatus.COMPLETED
        else:
            update_data["status"] = CommandStatus.FAILED

        command = await self.repo.update(db, command.id, update_data)

        logger.info(
            f"处理回调结果: {callback.command_id} -> {callback.result} "
            f"(耗时: {command.get_duration_ms()}ms)"
        )

        return command

    async def create_event_log(
        self,
        db: AsyncSession,
        event_request: EventRequest,
    ) -> DeviceEventLog:
        """
        创建事件日志记录

        只负责记录，不做业务处理。
        如果设备未提供时间戳，使用服务器当前时间。
        """
        # 处理时间戳：设备未提供则使用服务器时间
        if event_request.timestamp is None:
            event_timestamp = timezone.now_for_db()
            logger.debug(
                f"设备 {event_request.device_id} 未提供时间戳，使用服务器时间"
            )
        else:
            event_timestamp = timezone.to_utc(event_request.timestamp / 1000)

        event_log = DeviceEventLog(
            device_id=event_request.device_id,
            event_type=event_request.event_type,
            event_timestamp=event_timestamp,
            event_data=event_request.data,
            processed=False,
        )
        db.add(event_log)
        await db.flush()

        logger.info(
            f"记录设备事件: {event_request.device_id} -> "
            f"{event_request.event_type.value}"
        )

        return event_log

    async def update_event_log(
        self,
        db: AsyncSession,
        event_log: DeviceEventLog,
        processed: bool,
        processing_result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> DeviceEventLog:
        """
        更新事件日志处理状态
        """
        from sqlalchemy import update

        event_log.processed = processed
        if processing_result is not None:
            event_log.processing_result = processing_result
        if error_message is not None:
            event_log.error_message = error_message

        # 使用 SQLAlchemy update 直接更新
        stmt = (
            update(DeviceEventLog)
            .where(DeviceEventLog.id == event_log.id)
            .values(
                processed=processed,
                processing_result=processing_result,
                error_message=error_message,
            )
        )
        await db.execute(stmt)

        # 刷新实例以获取更新后的值
        await db.refresh(event_log)

        return event_log

    # ==================== 指令状态管理 ====================

    async def cancel_command(
        self,
        db: AsyncSession,
        command_id: str,
    ) -> DeviceCommand:
        """取消正在执行的指令"""
        command = await self.repo.get_by_command_id(db, command_id)
        if not command:
            raise NotFoundException(f"指令不存在: {command_id}")

        # 检查是否可以取消
        if command.status not in [
            CommandStatus.PENDING,
            CommandStatus.SENT,
            CommandStatus.ACK_RECEIVED,
        ]:
            raise ValueError(
                f"指令状态不允许取消: {command.status.value}"
            )

        # 更新状态
        command = await self.repo.update(
            db, command.id, {"status": CommandStatus.CANCELLED}
        )

        logger.info(f"指令已取消: {command_id}")
        return command

    async def retry_command(
        self,
        db: AsyncSession,
        command_id: str,
    ) -> DeviceCommand:
        """重试失败的指令"""
        command = await self.repo.get_by_command_id(db, command_id)
        if not command:
            raise NotFoundException(f"指令不存在: {command_id}")

        # 检查是否可以重试
        if not command.can_retry():
            raise ValueError(
                f"指令不允许重试: status={command.status.value}, "
                f"retry_count={command.retry_count}"
            )

        # 重置状态
        update_data = {
            "status": CommandStatus.PENDING,
            "sent_at": None,
            "ack_received_at": None,
            "completed_at": None,
            "result": None,
            "result_data": None,
            "error_detail": None,
            "retry_count": command.retry_count + 1,
        }

        command = await self.repo.update(db, command.id, update_data)

        logger.info(f"指令已重置: {command_id} (重试次数: {command.retry_count})")
        return command

    async def get_command_by_id(
        self,
        db: AsyncSession,
        command_id: str,
    ) -> DeviceCommand | None:
        """根据 command_id 查询指令"""
        return await self.repo.get_by_command_id(db, command_id)

    # ==================== 辅助方法 ====================

    def _generate_command_id(self, _device_id: str, task_type: str) -> str:
        """生成指令 ID"""
        date_str = timezone.now_for_db().strftime("%Y%m%d")
        unique_id = uuid.uuid4().hex[:8].upper()
        return f"CMD-{date_str}-{task_type}-{unique_id}"

    async def _get_device_url(
        self, _db: AsyncSession, device_id: str
    ) -> str:
        """
        获取设备 URL

        TODO: 从 Device 表查询设备的 base_url
        """
        # 简化处理：返回默认 URL
        return f"http://{device_id}:8080"

    async def _update_sent_status(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        ack_code: int,
        ack_message: str,
        trace_id: str | None,
    ) -> None:
        """更新指令发送状态"""
        update_data = {
            "status": CommandStatus.SENT,
            "sent_at": timezone.now_for_db(),
            "ack_code": ack_code,
            "ack_message": ack_message,
            "ack_trace_id": trace_id,
        }

        if ack_code == 200:
            update_data["status"] = CommandStatus.ACK_RECEIVED
            update_data["ack_received_at"] = timezone.now_for_db()

        await self.repo.update(db, command.id, update_data)

    async def _update_command_status(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        status: CommandStatus,
        error_detail: str | None = None,
    ) -> None:
        """更新指令状态"""
        update_data = {"status": status}
        if error_detail:
            update_data["error_detail"] = error_detail

        await self.repo.update(db, command.id, update_data)


# 创建单例
device_command_service = DeviceCommandService()


__all__ = [
    "DeviceCommandService",
    "device_command_service",
]
