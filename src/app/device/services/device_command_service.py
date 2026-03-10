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
from src.database.redis_cache import get_cache
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

    async def _invalidate_command_cache(self, command_id: int | None = None, invalidate_list: bool = False) -> None:
        """清理指令详情/列表缓存。"""
        await self.invalidate_cache(get_cache(), command_id, invalidate_list=invalidate_list)

    # ==================== CRUD 操作 ====================

    async def create_command(
        self,
        db: AsyncSession,
        command_request: CommandRequest,
    ) -> DeviceCommand | None:
        """创建设备指令"""
        # 生成 command_code（如果未提供）
        command_code = command_request.command_code
        if not command_code:
            command_code = self._generate_command_code(
                command_request.device_id,
                command_request.task_type.value,
            )

        # 生成 correlation_id（如果未提供）
        correlation_id = command_request.correlation_id
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # 创建指令记录
        command_data = {
            "command_code": command_code,
            "device_id": command_request.device_id,
            "task_type": command_request.task_type,
            "priority": command_request.priority,
            "timeout_ms": command_request.timeout_ms,
            "params": command_request.params,
            "correlation_id": correlation_id,
            "status": CommandStatus.PENDING,
        }

        command = await self.repo.create(db, command_data)
        if command:
            await self._invalidate_command_cache(invalidate_list=True)
            logger.info(f"创建指令: {command_code} -> {command_request.task_type.value}")

        return command

    async def send_command(
        self,
        db: AsyncSession,
        command_code: str,
        device_url: str | None = None,
    ) -> CommandAck:
        """
        发送指令到设备

        Args:
            db: 数据库会话
            command_code: 指令编码
            device_url: 设备 URL（可选）

        Returns:
            设备返回的 ACK 确认
        """
        # 1. 获取指令
        command = await self.repo.get_by_command_code(db, command_code)
        if not command:
            raise NotFoundException(f"指令不存在: {command_code}")

        # 2. 获取设备 URL
        if not device_url:
            device_url = await self._get_device_url(db, command.device_id)

        # 3. 构建请求体（白皮书 3.1.1 格式）
        request_body = {
            "command_code": command.command_code,
            "task_type": command.task_type.value,
            "priority": command.priority,
            "timeout": command.timeout_ms,
            "params": command.params,
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
        }

        # 4. 发送 HTTP 请求
        url = f"{device_url}/api/v1/device/command"
        logger.info(f"发送指令到设备: {url} -> {command_code}")

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.post(url, json=request_body)
                response.raise_for_status()

                ack_data = response.json()
                ack = CommandAck(**ack_data)

                # 5. 更新指令状态
                await self._update_sent_status(db, command, ack.code, ack.message, ack.trace_id)

                logger.info(f"指令已发送: {command_code} -> ACK {ack.code} ({ack.message})")
                return ack

        except httpx.HTTPStatusError as e:
            logger.error(f"发送指令失败: {command_code} -> HTTP {e.response.status_code}")
            await self._update_command_status(db, command, CommandStatus.FAILED, error_detail=str(e))
            raise
        except httpx.RequestError as e:
            logger.error(f"发送指令失败: {command_code} -> 网络错误 {e}")
            await self._update_command_status(db, command, CommandStatus.TIMEOUT, error_detail=str(e))
            raise

    async def handle_callback_result(
        self,
        db: AsyncSession,
        callback: CommandCallbackResult,
    ) -> DeviceCommand | None:
        """处理设备回调结果（更新指令状态）"""
        # 1. 获取指令
        command = await self.repo.get_by_command_code(db, callback.command_code)
        if not command or not command.id:
            raise NotFoundException(f"回调指令不存在: {callback.command_code}")

        # 2. 解析完成时间（使用 UTC 时区，转换为 naive 用于数据库）
        aware_dt = timezone.to_utc(int(callback.finish_time / 1000))
        completed_at = aware_dt.replace(tzinfo=None)

        # 3. 更新指令状态
        update_data = {
            "result": callback.result,
            "completed_at": completed_at,
            "result_data": callback.data,
            "error_detail": self._normalize_error_detail(callback.error_detail),
            "version": command.version,  # 乐观锁：必须包含当前版本号
        }

        if callback.result == "SUCCESS":
            update_data["status"] = CommandStatus.COMPLETED
        else:
            update_data["status"] = CommandStatus.FAILED

        updated_command = await self.repo.update(db, command.id, update_data)
        if updated_command:
            await self._invalidate_command_cache(updated_command.id, invalidate_list=True)
            logger.info(
                f"处理回调结果: {callback.command_code} -> {callback.result} (耗时: {updated_command.get_duration_ms()}ms)"
            )

        return updated_command

    async def create_event_log(
        self,
        db: AsyncSession,
        event_request: EventRequest,
    ) -> DeviceEventLog:
        """
        创建事件日志记录

        只负责记录，不做业务处理。
        如果设备未提供时间戳，使用服务器当前时间。

        注意：
        - event_request.device_code 是设备编码（字符串）
        - 数据库存储的是 Device.id（整数）
        - 需要通过 device_code 查找对应的 Device.id
        """
        # 通过 device_code 查找 device.id
        from src.app.device.repositories.device_repository import device_repository

        device = await device_repository.get_by_device_code(db, event_request.device_code)
        if not device or not device.id:
            raise NotFoundException(f"设备不存在: {event_request.device_code}")

        # 处理时间戳：设备未提供则使用服务器时间
        if event_request.timestamp is None:
            event_timestamp = timezone.now_for_db()
            logger.debug(f"设备 {event_request.device_code} 未提供时间戳，使用服务器时间")
        else:
            # 将 Unix 时间戳（毫秒）转换为 naive UTC datetime
            # timezone.to_utc 返回 aware datetime，需要转换为 naive 用于数据库存储
            aware_dt = timezone.to_utc(int(event_request.timestamp / 1000))
            event_timestamp = aware_dt.replace(tzinfo=None)

        event_log = DeviceEventLog(
            device_id=device.id,  # 存储 Device.id 而非 device_code
            event_type=event_request.event_type,
            event_timestamp=event_timestamp,
            event_data=event_request.data,
            processed=False,
        )
        db.add(event_log)
        await db.flush()

        logger.info(f"记录设备事件: {event_request.device_code} -> {event_request.event_type.value}")

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
            .where(DeviceEventLog.id == event_log.id)  # type: ignore[arg-type]
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
        command_code: str,
    ) -> DeviceCommand | None:
        """取消正在执行的指令"""
        command = await self.repo.get_by_command_code(db, command_code)
        if not command or not command.id:
            raise NotFoundException(f"指令不存在: {command_code}")

        # 检查是否可以取消
        if command.status not in [
            CommandStatus.PENDING,
            CommandStatus.SENT,
            CommandStatus.ACK_RECEIVED,
        ]:
            raise ValueError(f"指令状态不允许取消: {command.status.value}")

        # 更新状态
        updated_command = await self.repo.update(
            db,
            command.id,
            {
                "status": CommandStatus.CANCELLED,
                "version": command.version,
            },
        )

        if updated_command:
            await self._invalidate_command_cache(updated_command.id, invalidate_list=True)
            logger.info(f"指令已取消: {command_code}")
        return updated_command

    async def retry_command(
        self,
        db: AsyncSession,
        command_code: str,
    ) -> DeviceCommand | None:
        """重试失败的指令"""
        command = await self.repo.get_by_command_code(db, command_code)
        if not command or not command.id:
            raise NotFoundException(f"指令不存在: {command_code}")

        # 检查是否可以重试
        if not command.can_retry():
            raise ValueError(f"指令不允许重试: status={command.status.value}, retry_count={command.retry_count}")

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
            "version": command.version,
        }

        updated_command = await self.repo.update(db, command.id, update_data)

        if updated_command:
            await self._invalidate_command_cache(updated_command.id, invalidate_list=True)
            logger.info(f"指令已重置: {command_code} (重试次数: {updated_command.retry_count})")
        return updated_command

    async def get_command_by_code(
        self,
        db: AsyncSession,
        command_code: str,
    ) -> DeviceCommand | None:
        """根据 command_code 查询指令"""
        return await self.repo.get_by_command_code(db, command_code)

    # ==================== 辅助方法 ====================

    def _generate_command_code(self, _device_id: int, task_type: str) -> str:
        """生成指令编码"""
        date_str = timezone.now_for_db().strftime("%Y%m%d")
        unique_id = uuid.uuid4().hex[:8].upper()
        return f"CMD-{date_str}-{task_type}-{unique_id}"

    async def _get_device_url(self, db: AsyncSession, device_id: int) -> str:
        """
        获取设备 URL

        TODO: 从 Device 表查询设备的 base_url
        """
        from src.app.device.repositories.device_repository import device_repository

        device = await device_repository.get_by_id(db, device_id)
        if not device:
            logger.warning(f"设备不存在，使用兜底 URL: device_id={device_id}")
            return f"http://{device_id}:8080"

        # 优先使用设备通信配置（内部统一使用 device_id）
        if device.host:
            scheme = (device.protocol or "HTTP").lower()
            if scheme not in {"http", "https"}:
                scheme = "http"
            port = device.port or (443 if scheme == "https" else 80)
            return f"{scheme}://{device.host}:{port}"

        # E2E 测试环境 Mock 服务 URL 映射（按设备编码）
        mock_device_urls = {
            "ROBOT-ARM-01": "http://wes_mock_robot_arm_test:8004",
            "CONVEYOR-CAMERA-01": "http://wes_mock_camera_test:8003",
            "CAMERA-CONVEYOR-01": "http://wes_mock_camera_test:8003",
        }
        if device.device_code in mock_device_urls:
            return mock_device_urls[device.device_code]

        # 默认 URL 格式（按设备编码）
        return f"http://{device.device_code}:8080"

    async def _update_sent_status(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        ack_code: int,
        ack_message: str,
        trace_id: str | None,
    ) -> None:
        """更新指令发送状态"""
        if not command or not command.id:
            raise NotFoundException(f"指令不存在: {command.command_code}")

        update_data = {
            "status": CommandStatus.SENT,
            "sent_at": timezone.now_for_db(),
            "ack_code": ack_code,
            "ack_message": ack_message,
            "ack_trace_id": trace_id,
            "version": command.version,  # 乐观锁：必须包含当前版本号
        }

        if ack_code == 200:
            update_data["status"] = CommandStatus.ACK_RECEIVED
            update_data["ack_received_at"] = timezone.now_for_db()

        updated_command = await self.repo.update(db, command.id, update_data)
        if updated_command:
            await self._invalidate_command_cache(updated_command.id, invalidate_list=True)

    async def _update_command_status(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        status: CommandStatus,
        error_detail: dict[str, Any] | str | None = None,
    ) -> None:
        """更新指令状态"""
        if not command or not command.id:
            raise NotFoundException(f"指令不存在: {command.command_code}")

        update_data = {
            "status": status,
            "version": command.version,  # 乐观锁：必须包含当前版本号
        }
        normalized_error = self._normalize_error_detail(error_detail)
        if normalized_error is not None:
            update_data["error_detail"] = normalized_error

        updated_command = await self.repo.update(db, command.id, update_data)
        if updated_command:
            await self._invalidate_command_cache(updated_command.id, invalidate_list=True)

    def _normalize_error_detail(self, error_detail: dict[str, Any] | str | None) -> dict[str, Any] | None:
        """将错误详情统一为 JSON 对象。"""
        if error_detail is None:
            return None
        if isinstance(error_detail, dict):
            return error_detail
        return {"message": str(error_detail)}


# 创建单例
device_command_service = DeviceCommandService()


__all__ = [
    "DeviceCommandService",
    "device_command_service",
]
