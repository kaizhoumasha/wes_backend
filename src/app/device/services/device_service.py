"""
设备 Service 层

包含设备管理、指令管理、回调处理等核心业务逻辑

遵循项目标准架构：
- DeviceService 继承 BaseService，获得标准 CRUD 能力
- DeviceCommandService 独立管理指令下发逻辑
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.device import (
    AckResponse,
    CommandRequest,
    CommandResponse,
    Device,
    DeviceCommand,
    DeviceCommandAck,
    DeviceCommandPayload,
    DeviceStatusResponse,
    EventCallbackRequest,
    ResultCallbackRequest,
)
from src.app.device.repositories.device_repository import (
    device_command_repository,
    device_event_repository,
    device_repository,
)
from src.core.base_service import BaseService
from src.core.exceptions import NotFoundException
from src.core.logger import logger
from src.utils.event_publisher import publish_business_status, publish_system_notification
from src.utils.snowflake import generate_snowflake_id


# ============================================================================
# 设备管理 Service（继承 BaseService 获得标准 CRUD）
# ============================================================================

class DeviceService(BaseService[Device, device_repository]):  # type: ignore[valid-type]
    """设备管理 Service

    继承 BaseService，自动获得标准 CRUD 能力：
    - get_by_id, get_list, create, update, delete
    - 缓存管理
    - 软删除支持
    """

    def __init__(self):
        super().__init__(device_repository, enable_cache=True, cache_prefix="sys:device:detail")

    # 额外扩展方法
    async def get_by_device_id(self, db: AsyncSession, device_id: str) -> Device:
        """根据设备 ID 获取设备"""
        device = await self.repo.get_by_device_id(db, device_id)
        if not device:
            raise NotFoundException(f"设备 {device_id} 不存在")
        return device

    async def update_status(
        self,
        db: AsyncSession,
        device_id: str,
        status: str,
        current_command_id: str | None = None,
        cache=None,
    ) -> None:
        """更新设备状态"""
        await self.repo.update_status(db, device_id, status, current_command_id)
        # 清除缓存
        await self.invalidate_cache(cache, device_id)

    async def heartbeat(self, db: AsyncSession, device_id: str, cache=None) -> None:
        """设备心跳"""
        await self.repo.set_online(db, device_id, is_online=True)
        # 清除缓存
        await self.invalidate_cache(cache, device_id)


# 创建单例
device_service = DeviceService()


# ============================================================================
# 设备指令管理 Service（独立管理指令下发）
# ============================================================================

class DeviceCommandService:
    """设备指令管理 Service

    负责指令的下发、重试、状态追踪等
    不继承 BaseService，因为指令管理有特殊的业务逻辑
    """

    def __init__(self):
        self.repo = device_command_repository
        self.device_repo = device_repository
        self._http_client: httpx.AsyncClient | None = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端"""
        if self._http_client is None:
            timeout = httpx.Timeout(10.0, connect=5.0)
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            self._http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
        return self._http_client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def create_command(
        self,
        db: AsyncSession,
        request: CommandRequest,
    ) -> DeviceCommand:
        """创建新指令

        Args:
            db: 数据库会话
            request: 指令请求

        Returns:
            创建的指令记录
        """
        # 验证设备存在
        device = await device_repository.get_by_device_id(db, request.device_id)
        if not device:
            raise NotFoundException(f"设备 {request.device_id} 不存在")

        # 生成唯一指令 ID
        command_id = f"CMD-{generate_snowflake_id()}"

        # 创建指令记录
        command_data = request.model_dump()
        command_data["command_id"] = command_id
        command_data["status"] = "PENDING"

        command = await self.repo.create(db, command_data)
        logger.info(f"创建指令: {command_id} -> 设备 {request.device_id}")

        return command

    async def send_command(
        self,
        db: AsyncSession,
        command_id: str,
    ) -> DeviceCommandAck:
        """发送指令到设备

        Args:
            db: 数据库会话
            command_id: 指令 ID

        Returns:
            设备的 ACK 响应
        """
        # 获取指令记录
        command = await self.repo.get_by_command_id(db, command_id)
        if not command:
            raise NotFoundException(f"指令 {command_id} 不存在")

        # 获取设备信息
        device = await device_repository.get_by_device_id(db, command.device_id)
        if not device:
            raise NotFoundException(f"设备 {command.device_id} 不存在")

        # 构建指令 Payload（白皮书格式）
        payload = DeviceCommandPayload(
            command_id=command.command_id,
            task_type=command.task_type,
            priority=command.priority,
            timeout=command.timeout_ms,
            params=command.params or {},
            timestamp=int(command.created_at.timestamp() * 1000),
        )

        # 发送 HTTP 请求到设备
        url = f"{device.protocol.lower()}://{device.ip_address}:{device.port}/api/v1/device/command"

        headers = {}
        if device.auth_token:
            headers["Authorization"] = f"Bearer {device.auth_token}"

        try:
            response = await self.http_client.post(
                url,
                json=payload.model_dump(),
                headers=headers,
                timeout=device.timeout_seconds,
            )
            response.raise_for_status()

            # 解析 ACK
            ack_data = response.json()
            ack = DeviceCommandAck(**ack_data)

            # 更新指令状态
            if ack.code == 200:
                await self.repo.update_status(
                    db,
                    command_id,
                    "ACKED",
                    raw_response=ack_data,
                    device_trace_id=ack.trace_id,
                )

                # 更新设备状态
                await device_repository.update_status(
                    db,
                    command.device_id,
                    "RUNNING",
                    current_command_id=command_id,
                )

                logger.info(f"指令已确认: {command_id} -> {ack.message}")
            else:
                await self.repo.update_status(
                    db,
                    command_id,
                    "FAILED",
                    error_message=ack.message,
                    raw_response=ack_data,
                )
                logger.warning(f"指令被拒绝: {command_id} -> {ack.message}")

            return ack

        except httpx.TimeoutException:
            # 超时处理
            await self.repo.update_status(db, command_id, "TIMEOUT", error_message="请求超时")
            logger.error(f"指令发送超时: {command_id}")

            return DeviceCommandAck(code=504, message="Request Timeout")

        except httpx.HTTPError as e:
            # HTTP 错误处理
            await self.repo.update_status(db, command_id, "FAILED", error_message=str(e))
            logger.error(f"指令发送失败: {command_id} -> {e}")

            return DeviceCommandAck(code=500, message=str(e))

    async def retry_command(
        self,
        db: AsyncSession,
        command_id: str,
    ) -> DeviceCommandAck | None:
        """重试指令发送

        Args:
            db: 数据库会话
            command_id: 指令 ID

        Returns:
            设备的 ACK 响应，重试次数超限返回 None
        """
        command = await self.repo.get_by_command_id(db, command_id)
        if not command:
            return None

        device = await device_repository.get_by_device_id(db, command.device_id)
        if not device:
            return None

        # 检查重试次数
        retry_count = await self.repo.increment_retry(db, command_id)
        if retry_count > device.max_retry:
            await self.repo.update_status(db, command_id, "FAILED", error_message="重试次数超限")
            logger.error(f"指令重试次数超限: {command_id}")
            return None

        # 重新发送
        return await self.send_command(db, command_id)

    async def cancel_command(
        self,
        db: AsyncSession,
        command_id: str,
    ) -> bool:
        """取消指令

        Args:
            db: 数据库会话
            command_id: 指令 ID

        Returns:
            是否取消成功
        """
        command = await self.repo.get_by_command_id(db, command_id)
        if not command:
            return False

        # 如果指令已发送，需要通知设备
        if command.status in ("SENT", "ACKED"):
            device = await device_repository.get_by_device_id(db, command.device_id)
            if device:
                url = f"{device.protocol.lower()}://{device.ip_address}:{device.port}/api/v1/device/cancel"

                try:
                    await self.http_client.post(
                        url,
                        json={"command_id": command_id},
                        timeout=5.0,
                    )
                except Exception as e:
                    logger.warning(f"取消指令通知设备失败: {command_id} -> {e}")

        # 更新指令状态
        await self.repo.update_status(db, command_id, "CANCELLED")

        # 释放设备状态
        await device_repository.update_status(
            db,
            command.device_id,
            "IDLE",
            current_command_id=None,
        )

        logger.info(f"指令已取消: {command_id}")
        return True

    async def query_device_status(
        self,
        db: AsyncSession,
        device_id: str,
    ) -> DeviceStatusResponse | None:
        """查询设备状态

        Args:
            db: 数据库会话
            device_id: 设备 ID

        Returns:
            设备状态响应
        """
        device = await device_repository.get_by_device_id(db, device_id)
        if not device:
            return None

        url = f"{device.protocol.lower()}://{device.ip_address}:{device.port}/api/v1/device/status"

        try:
            response = await self.http_client.get(url, timeout=5.0)
            response.raise_for_status()

            status_data = response.json()
            return DeviceStatusResponse(**status_data)

        except Exception as e:
            logger.error(f"查询设备状态失败: {device_id} -> {e}")

            # 标记设备离线
            await device_repository.set_online(db, device_id, is_online=False)

            return None


class DeviceCallbackService:
    """设备回调处理 Service

    处理设备上报的任务结果和事件
    """

    async def handle_result_callback(
        self,
        db: AsyncSession,
        request: ResultCallbackRequest,
    ) -> AckResponse:
        """处理任务结果回调（白皮书 3.2.1）

        Args:
            db: 数据库会话
            request: 结果回调请求

        Returns:
            ACK 确认响应
        """
        # 查找指令记录
        command = await device_command_repository.get_by_command_id(db, request.command_id)
        if not command:
            logger.warning(f"收到未知指令的结果回调: {request.command_id}")
            return AckResponse(code=404, message="Command Not Found")

        # 更新指令结果
        update_data: dict[str, object] = {
            "result": request.result,
            "result_data": request.data,
            "completed_at": request.finish_time,
        }

        if request.result == "FAILED" and request.error_detail:
            update_data["error_code"] = request.error_detail.get("code")
            update_data["error_message"] = request.error_detail.get("msg")

        await device_command_repository.update_status(
            db,
            request.command_id,
            "COMPLETED" if request.result == "SUCCESS" else "FAILED",
            **update_data,
        )

        # 释放设备状态
        await device_repository.update_status(
            db,
            request.device_id,
            "IDLE",
            current_command_id=None,
        )

        # 发布 SSE 事件（通知前端）
        await publish_business_status(
            business_type="device_command",
            business_id=int(request.command_id.split("-")[-1]),
            status=request.result,
            extra={
                "device_id": request.device_id,
                "task_type": command.task_type,
                "result_data": request.data,
            },
        )

        logger.info(f"任务结果已处理: {request.command_id} -> {request.result}")

        return AckResponse(code=200, message="ACK")

    async def handle_event_callback(
        self,
        db: AsyncSession,
        request: EventCallbackRequest,
    ) -> AckResponse:
        """处理设备事件上报（白皮书 3.2.2）

        Args:
            db: 数据库会话
            request: 事件上报请求

        Returns:
            ACK 确认响应
        """
        # 验证设备存在
        device = await device_repository.get_by_device_id(db, request.device_id)
        if not device:
            logger.warning(f"收到未知设备的事件上报: {request.device_id}")
            return AckResponse(code=404, message="Device Not Found")

        # 记录事件
        # 注意：不传递 created_at，使用 DataTableMixin 的 default_factory 自动生成
        event_data = {
            "device_id": request.device_id,
            "event_type": request.event_type,
            "event_data": request.data,
        }

        event = await device_event_repository.create(db, event_data)

        # 发布 SSE 事件（通知前端）
        await publish_system_notification(
            title=f"设备事件: {request.device_id}",
            message=f"{request.event_type}: {request.data or '无额外数据'}",
            level="info",
        )

        logger.info(f"设备事件已记录: {request.device_id} -> {request.event_type}")

        # ⭐ 触发 Celery 异步任务（立即返回，不等待处理完成）
        # 符合白皮书异步事件驱动模式：快速响应 + 异步处理
        if request.event_type == "MATERIAL_ARRIVED":
            from src.celery_app.tasks.device import process_material_arrived

            # 构造任务参数
            task_params = {
                "device_id": request.device_id,
                "event_type": request.event_type,
                "barcode": request.data.get("barcode") if request.data else None,
                "location": request.data.get("location") if request.data else None,
            }

            # 异步发送任务（不阻塞 HTTP 响应）
            process_material_arrived.apply_async(
                args=[task_params],
                task_id=f"event-{event.id}",  # 使用事件ID作为任务ID，便于追踪
            )

            logger.info(f"料盘到达事件已提交异步处理: event_id={event.id}, barcode={task_params.get('barcode')}")

        # 预留其他事件类型的处理入口
        # elif request.event_type == "SCAN_COMPLETED":
        #     from src.celery_app.tasks.device import process_scan_completed
        #     process_scan_completed.apply_async(args=[task_params])

        # 立即返回 ACK（符合白皮书异步事件驱动模式）
        return AckResponse(code=200, message="ACK")


# 创建单例
device_command_service = DeviceCommandService()
device_callback_service = DeviceCallbackService()
