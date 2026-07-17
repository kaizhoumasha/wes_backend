"""设备指令服务 (Device Command Service)

提供设备指令的 CRUD 操作, 专注于数据访问层。

职责划分:
- Service 层: CRUD 操作(创建, 查询, 更新, 删除)
- Celery 任务层: 业务逻辑(SDAF 流程: 验证, 决策, 构建, 发送)
"""

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import (
    CommandAck,
    CommandCallbackResult,
    CommandRequest,
    CommandStatus,
    DeviceCommand,
)
from src.app.device.repositories.command_repository import (
    DeviceCommandRepository,
    device_command_repository,
)
from src.app.device.repositories.device_repository import DeviceRepository, device_repository
from src.core.base_service import BaseService
from src.core.exceptions import NotFoundException
from src.core.logger import logger
from src.database.redis_cache import get_cache
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_str


@dataclass(frozen=True)
class DeviceCallbackResultOutcome:
    """设备结果回调处理结果。"""

    command: DeviceCommand
    late_callback_recorded: bool = False


class StaleDeviceCommandPrecondition(ValueError):
    """锁定后的设备事实与已准入前置条件不一致。"""


def _device_command_result_trace_id(command: object, callback: CommandCallbackResult) -> str | None:
    return (
        coerce_optional_str(getattr(callback, "trace_id", None))
        or coerce_optional_str(getattr(command, "trace_id", None))
        or _device_command_result_source_event_id(command, callback)
    )


def _device_command_result_correlation_id(command: object, callback: CommandCallbackResult) -> str | None:
    command_code = coerce_optional_str(getattr(command, "command_code", None)) or coerce_optional_str(
        getattr(callback, "command_code", None)
    )
    return (
        coerce_optional_str(getattr(command, "correlation_id", None))
        or coerce_optional_str(getattr(callback, "causation_id", None))
        or (f"command:{command_code}" if command_code else None)
    )


def _device_command_result_source_event_id(_command: object, callback: CommandCallbackResult) -> str:
    """结果观测与 RuntimeInbox 共用供应商提供的唯一事件身份。"""

    return callback.source_event_id


def _emit_device_command_result_observability(command: object, callback: CommandCallbackResult) -> None:
    """发出 DeviceCommand RESULT 观测事件；观测失败不改变结果处理状态。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    command_code = coerce_optional_str(getattr(command, "command_code", None)) or coerce_optional_str(
        getattr(callback, "command_code", None)
    )
    try:
        _ = runtime_observability_registry.emit(
            "device_command.result",
            {
                "trace_id": _device_command_result_trace_id(command, callback),
                "correlation_id": _device_command_result_correlation_id(command, callback),
                "command_code": command_code,
                "source_event_id": _device_command_result_source_event_id(command, callback),
            },
        )
    except Exception as exc:  # pragma: no cover - 防止观测链路反向影响设备结果处理
        logger.warning(f"设备指令 RESULT 观测事件发射失败: command_code={command_code or 'UNKNOWN'}, error={exc}")


class DeviceCommandService(BaseService[DeviceCommand, DeviceCommandRepository]):
    """设备指令服务。

    常规 CRUD 与发送流程保持原有职责；Runtime 副作用入口只在外层事务中
    锁定并校验权威设备事实，然后准备 DeviceCommand 与 Outbox，不执行外部 I/O。
    """

    def __init__(
        self,
        repository: DeviceCommandRepository = device_command_repository,
        device_repository: DeviceRepository = device_repository,
    ) -> None:
        """初始化服务"""
        super().__init__(
            repository,
            enable_cache=True,
            cache_prefix="app:device:command",
        )
        self.device_repository = device_repository
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
        task_type = self._task_type_value(command_request.task_type)
        command_code = command_request.command_code
        if not command_code:
            command_code = self._generate_command_code(
                command_request.device_id,
                task_type,
            )

        # 生成 trace_id（如果未提供）
        trace_id = command_request.trace_id
        if not trace_id:
            trace_id = str(uuid.uuid4())

        # 创建指令记录
        command_data: dict[str, Any] = {
            "command_code": command_code,
            "device_id": command_request.device_id,
            "task_type": task_type,
            "priority": command_request.priority,
            "timeout_ms": command_request.timeout_ms,
            "params": cast("dict[str, Any] | None", command_request.params),
            "trace_id": trace_id,
            "status": CommandStatus.PENDING,
        }

        command = await self.repo.create(db, command_data)
        if command:
            await db.commit()
            await self._invalidate_command_cache(invalidate_list=True)
            logger.info(f"创建指令: {command_code} -> {task_type}")

        return command

    async def prepare_runtime_effect(
        self,
        db: AsyncSession,
        *,
        request: Any,
        target_device_id: int | None,
        target_device_code: str | None,
        expected_fact_version: str,
        expected_available: bool,
        session: Any,
        workline: Any,
        idempotency_key: str,
        trace_id: str | None,
    ) -> tuple[DeviceCommand, Any]:
        """在 Runtime 外层事务内原子准备 DeviceCommand + Outbox，只 flush。"""

        from hashlib import sha256

        from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType

        target_device = await self.device_repository.get_runtime_effect_target_for_update(
            db,
            target_device_id=target_device_id,
            target_device_code=target_device_code,
        )
        if target_device is None:
            raise StaleDeviceCommandPrecondition("device target no longer exists")
        actual_version = getattr(target_device, "version", None)
        actual_fact_version = (
            f"device:v{actual_version}"
            if isinstance(actual_version, int) and not isinstance(actual_version, bool) and actual_version >= 0
            else "device:v-1"
        )
        status = getattr(target_device, "device_status", None)
        actual_available = (
            getattr(target_device, "is_active", None) is True
            and str(getattr(status, "value", status)) == "IDLE"
            and getattr(target_device, "maintenance_mode", None) is False
            and getattr(target_device, "current_command_id", None) is None
        )
        if actual_fact_version != expected_fact_version or actual_available != expected_available:
            raise StaleDeviceCommandPrecondition("device fact changed")
        if not actual_available:
            raise StaleDeviceCommandPrecondition("device is unavailable for command write")

        device_id = getattr(target_device, "id", None)
        device_code = getattr(target_device, "device_code", None)
        if not isinstance(device_id, int) or not isinstance(device_code, str) or not device_code:
            raise ValueError("runtime device command requires a pinned target device")
        requested_code = getattr(request, "command_code", None)
        command_code = requested_code or f"SC-{sha256(idempotency_key.encode('utf-8')).hexdigest()[:32]}"
        command = DeviceCommand(
            command_code=command_code,
            device_id=device_id,
            task_type=str(request.action),
            priority=int(request.priority),
            timeout_ms=int(request.timeout_ms),
            params=dict(request.payload),
            trace_id=trace_id,
            correlation_id=idempotency_key[:120],
            workline_id=getattr(workline, "id", None) or getattr(session, "workline_id", None),
            plugin_key=getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None),
            contract_version=getattr(session, "contract_version", None),
            status=CommandStatus.PENDING,
        )
        outbox = SystemOutbox(
            session_id=getattr(session, "id", None),
            workline_id=getattr(workline, "id", None) or getattr(session, "workline_id", None),
            device_id=device_id,
            operation_domain="DEVICE",
            operation_key=idempotency_key,
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            dispatch_key=f"device-command:{command_code}",
            target_type=SystemOutboxTargetType.DEVICE,
            target_code=device_code,
            payload_json={
                "command_code": command_code,
                "device_code": device_code,
                "task_type": str(request.action),
                "priority": int(request.priority),
                "timeout": int(request.timeout_ms),
                "params": dict(request.payload),
                "trace_id": trace_id,
            },
            trace_id=trace_id,
        )
        await self.repo.add_runtime_effect(db, command, outbox)
        return command, outbox

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

        # 2. 获取设备命令端点与设备编码
        device_endpoint, device_code = await self._get_device_command_endpoint(db, command.device_id, device_url)

        # 3. 构建请求体（白皮书 3.1.1 格式）
        request_body: dict[str, Any] = {
            "device_code": device_code,
            "command_code": command.command_code,
            "task_type": self._task_type_value(command.task_type),
            "priority": command.priority,
            "timeout": command.timeout_ms,
            "params": cast("dict[str, Any] | None", command.params),
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
        }

        # 4. 发送 HTTP 请求
        url = device_endpoint
        logger.info(f"发送指令到设备: {url} -> {command_code}")

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.post(url, json=request_body)
                _ = response.raise_for_status()

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
    ) -> DeviceCallbackResultOutcome:
        """处理设备回调结果（更新指令状态）"""
        # 1. 获取指令
        command = await self.repo.get_by_command_code(db, callback.command_code)
        if not command or not command.id:
            raise NotFoundException(f"回调指令不存在: {callback.command_code}")

        # RuntimeReconciliationFacade 已物理删除,device 域直接走
        # workline shim 路径(impl 模块在 sys.modules 上替换此 shim,行为等价)。
        # 测试契约 `tests/contracts/device/test_device_command_service_contract.py` 已用同一路径。
        from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
            workline_runtime_reconciliation_service,
        )

        if await workline_runtime_reconciliation_service.record_late_callback_if_pending(
            db,
            command=command,
            callback_payload=callback.model_dump(mode="json"),
        ):
            logger.warning(f"迟到 callback 已记录为对账证据，跳过自动更新指令状态: {callback.command_code}")
            return DeviceCallbackResultOutcome(command=command, late_callback_recorded=True)

        # 2. 解析完成时间（finish_time 为 Unix 毫秒，数据库存 UTC naive）
        completed_at = timezone.to_db_datetime(callback.finish_time / 1000)
        if completed_at is None:
            raise ValueError(f"无效的回调完成时间: {callback.finish_time}")

        # 3. 更新指令状态
        update_data: dict[str, Any] = {
            "result": callback.result,
            "completed_at": completed_at,
            "result_data": cast("dict[str, Any] | None", callback.data),
            "error_detail": self._normalize_error_detail(callback.error_detail),
        }

        if callback.result == "SUCCESS":
            update_data["status"] = CommandStatus.COMPLETED
        else:
            update_data["status"] = CommandStatus.FAILED

        updated_command = await self.repo.update(db, command.id, update_data)
        if updated_command:
            # 由调用方统一控制事务边界，避免在 trace 链中间提前提交。
            await self._invalidate_command_cache(updated_command.id, invalidate_list=True)
            logger.info(
                f"处理回调结果: {callback.command_code} -> {callback.result} (耗时: {updated_command.get_duration_ms()}ms)"
            )
            _emit_device_command_result_observability(updated_command, callback)

        if updated_command is None:
            raise RuntimeError(f"更新回调指令状态失败: {callback.command_code}")
        return DeviceCallbackResultOutcome(command=updated_command)

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
            },
        )

        if updated_command:
            await db.commit()
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
        update_data: dict[str, Any] = {
            "status": CommandStatus.PENDING,
            "sent_at": None,
            "ack_received_at": None,
            "completed_at": None,
            "result": None,
            "result_data": None,
            "error_detail": None,
            "retry_count": command.retry_count + 1,
        }

        updated_command = await self.repo.update(db, command.id, update_data)

        if updated_command:
            await db.commit()
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

    def _task_type_value(self, task_type: Any) -> str:
        """兼容历史 TaskType 枚举与插件自定义字符串。"""
        if isinstance(task_type, Enum):
            return str(task_type.value)
        return str(task_type)

    async def _get_device_command_endpoint(
        self,
        db: AsyncSession,
        device_id: int,
        device_url: str | None = None,
    ) -> tuple[str, str]:
        """获取设备命令端点和顶层 device_code。"""
        from src.app.device.repositories.device_repository import device_repository

        device = await device_repository.get_by_id(db, device_id)
        device_code = str(getattr(device, "device_code", device_id)) if device else str(device_id)

        if device_url:
            return f"{device_url.rstrip('/')}/api/v1/device/command", device_code

        base_url = await self._get_device_url(db, device_id, device=device)
        callback_path = "/api/v1/device/command"
        if device:
            raw_callback_path = getattr(device, "callback_path", None)
            if raw_callback_path:
                callback_path = str(raw_callback_path)
                if not callback_path.startswith("/"):
                    callback_path = f"/{callback_path}"
        return f"{base_url}{callback_path}", device_code

    async def _get_device_url(self, db: AsyncSession, device_id: int, device: Any | None = None) -> str:
        """
        获取设备基础 URL。
        """
        from src.app.device.repositories.device_repository import device_repository

        if device is None:
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

        update_data: dict[str, Any] = {
            "status": CommandStatus.SENT,
            "sent_at": timezone.now_for_db(),
            "ack_code": ack_code,
            "ack_message": ack_message,
            "ack_trace_id": trace_id,
        }

        ack_received_at = None
        if ack_code == 200:
            ack_received_at = timezone.now_for_db()
            update_data["status"] = CommandStatus.ACK_RECEIVED
            update_data["ack_received_at"] = ack_received_at

        updated_command = await self.repo.update(db, command.id, update_data)
        if updated_command is None or not isinstance(updated_command.id, int):
            return

        updated_command_id = updated_command.id
        if ack_received_at is not None:
            # facade 已物理删除,见上方同段注释。
            from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
                workline_runtime_reconciliation_service,
            )

            _ = await workline_runtime_reconciliation_service.activate_execution_deadline_after_ack(
                db,
                command_id=updated_command_id,
                ack_received_at=ack_received_at,
            )
        await db.commit()
        await self._invalidate_command_cache(updated_command_id, invalidate_list=True)

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

        update_data: dict[str, Any] = {
            "status": status,
        }
        normalized_error = self._normalize_error_detail(error_detail)
        if normalized_error is not None:
            update_data["error_detail"] = normalized_error

        updated_command = await self.repo.update(db, command.id, update_data)
        if updated_command:
            await db.commit()
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


__all__ = ["DeviceCallbackResultOutcome", "DeviceCommandService", "device_command_service"]


__all__ = [
    "DeviceCommandService",
    "device_command_service",
]
