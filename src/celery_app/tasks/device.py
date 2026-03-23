# ============================================
# 设备事件处理任务 - P9 WES Backend
# ============================================
# 用途: 处理设备事件的异步任务（料盘到达、扫码完成等）
# 架构: 插件化设备处理器，支持 SDAF 控制循环
# ============================================

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar, TypedDict, cast

from celery import Task  # pyright: ignore[reportMissingTypeStubs]
from loguru import logger

from src.celery_app.app import celery_app

T = TypeVar("T")


class ProcessDeviceEventResult(TypedDict, total=False):
    status: str
    message: str
    event_id: int | None
    commands_created: list[str]
    action_params: dict[str, Any] | None

# ============================================
# 设备任务基类 - 自动处理数据库会话
# ============================================


class DeviceTask(Task):
    """设备任务基类 - 提供数据库会话管理"""

    _db: Any | None = None

    @property
    def db(self) -> Any:
        """懒加载数据库会话"""
        if self._db is None:
            from src.database.db import AsyncSessionLocal as AsyncSessionLocalDynamic

            session_local = AsyncSessionLocalDynamic
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        """清理资源"""
        if self._db:
            asyncio.get_event_loop().run_until_complete(self._db.close())
            self._db = None

    def on_failure(
        self, exc: Exception, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any], einfo: Any
    ) -> None:
        """任务失败时清理资源"""
        self.cleanup()
        logger.error(f"任务 {task_id} 失败: {exc}")
        cast("Any", super()).on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval: Any, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """任务成功时清理资源"""
        self.cleanup()
        logger.info(f"任务 {task_id} 成功完成")
        cast("Any", super()).on_success(retval, task_id, args, kwargs)


def _run_async(coro: Awaitable[T]) -> T:
    """在 Celery 同步任务中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


async def _resolve_target_device_id(device_repo: Any, db: Any, action_params: dict[str, Any]) -> tuple[int, str]:
    """将外部动作参数（device_code）解析为内部 device_id。"""
    target_device_code = action_params.get("device_code")

    if isinstance(target_device_code, str) and target_device_code:
        target_device = await device_repo.get_by_device_code(db, target_device_code)
        if not target_device:
            raise ValueError(f"目标设备不存在: device_code={target_device_code}")
        return target_device.id, target_device.device_code

    raise ValueError("动作参数缺少目标设备编码（需要 device_code）")


# ============================================
# 统一设备事件处理任务（SDAF 流程在 Celery 中实现）
# ============================================


@celery_app.task(
    name="src.celery_app.tasks.device.process_device_event",
    base=DeviceTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_device_event(self: DeviceTask, event_data: dict[str, Any]) -> ProcessDeviceEventResult:
    """处理设备事件 - SDAF 流程实现

    SDAF 控制循环（在 Celery Worker 中执行）:
    1. SENSE (感知): 验证事件数据 → 记录 EventLog
    2. DECIDE (决策): 根据 WES 业务规则决定动作
    3. ACT (执行): 构建并发送指令到设备
    4. FEEDBACK (反馈): 更新事件处理状态

    Args:
        event_data: 事件数据字典
            - device_code: 设备编码
            - event_type: 事件类型
            - timestamp: 事件时间戳
            - data: 事件负载数据
            - request_id: 请求 ID（用于链路追踪）

    Returns:
        任务执行结果字典
    """
    # 恢复 request_id 到上下文（用于链路追踪）
    request_id = event_data.get("request_id")
    if request_id:
        try:
            from starlette_context import context as ctx

            ctx["request_id"] = request_id
            logger.debug(f"[Celery] 恢复 request_id 上下文: {request_id}")
        except Exception as e:
            logger.warning(f"[Celery] 无法设置 request_id 上下文: {e}")

    try:
        from src.app.device.repositories import DeviceRepository
        from src.app.device.services import device_command_service
        from src.device_processors import DeviceProcessorRegistry, register_builtin_processors

        # 确保处理器已注册
        if not DeviceProcessorRegistry.list_supported_types():
            register_builtin_processors()

        # 1. 解析事件数据
        device_code = event_data.get("device_code")
        event_type = event_data.get("event_type")
        timestamp = event_data.get("timestamp")  # 可选字段
        payload_data = event_data.get("data", {})
        data: dict[str, Any] = cast("dict[str, Any]", payload_data) if isinstance(payload_data, dict) else {}

        logger.info(
            f"[Celery] 开始处理设备事件: device_code={device_code}, "
            f"event_type={event_type}, timestamp_provided={timestamp is not None}, "
            f"request_id={request_id}"
        )

        # 2. SENSE: 验证必需字段（timestamp 可选）
        if not all([device_code, event_type]):
            error_msg = "事件数据缺少必需字段 (device_code, event_type)"
            logger.error(f"{error_msg}: {event_data}")
            return {
                "status": "error",
                "message": error_msg,
            }

        # 类型断言：验证后确保 device_code 和 event_type 不为 None
        assert device_code is not None
        assert event_type is not None
        assert isinstance(device_code, str)
        assert isinstance(event_type, str)

        # 3. 异步处理（在 Celery Worker 中）
        async def _process() -> ProcessDeviceEventResult:
            async with self.db as db:
                # SENSE: 记录事件日志
                from src.app.device.models.event_log import EventRequest, EventType

                event_request = EventRequest(
                    device_code=device_code,
                    event_type=EventType(event_type),
                    timestamp=timestamp,
                    data=data,
                )
                event_log = await device_command_service.create_event_log(db, event_request)

                # 获取设备信息
                device_repo = DeviceRepository()
                device = await device_repo.get_by_device_code(db, device_code)

                if not device:
                    raise ValueError(f"设备不存在: {device_code}")

                # 获取设备处理器
                processor = DeviceProcessorRegistry.get_processor_or_raise(device.device_type)

                # 构建完整的事件数据（保持嵌套结构，处理器期望 event_data.data）
                full_event_data: dict[str, Any] = {
                    "device_code": device_code,
                    "event_type": event_type,
                    "timestamp": timestamp,
                    "data": data,  # 保持嵌套结构
                }

                # SENSE: 验证事件
                is_valid, error_msg = await processor.validate_event(full_event_data)
                if not is_valid:
                    raise ValueError(f"事件数据验证失败: {error_msg}")

                # DECIDE: 决策动作
                action_params = await processor.decide_action(full_event_data)

                # ACT: 构建并发送指令
                commands_created: list[str] = []
                if action_params:
                    if not isinstance(action_params, dict):
                        raise ValueError("事件决策结果格式错误：action_params 必须是字典")

                    target_device_id, target_device_code = await _resolve_target_device_id(
                        device_repo,
                        db,
                        action_params,
                    )
                    internal_action_params = dict(action_params)
                    internal_action_params["device_id"] = target_device_id
                    internal_action_params["device_code"] = target_device_code

                    command_request = await processor.build_command(internal_action_params)
                    command = await device_command_service.create_command(db, command_request)
                    if command is None:
                        raise RuntimeError("创建设备指令失败")
                    commands_created.append(command.command_code)

                    # 发送指令
                    try:
                        _ = await device_command_service.send_command(db, command.command_code)
                    except Exception as e:
                        logger.error(f"发送指令失败: {e}")
                        # 继续处理，不影响事件记录

                # 更新事件处理状态
                _ = await device_command_service.update_event_log(
                    db,
                    event_log,
                    processed=True,
                    processing_result={
                        "action_params": action_params,
                        "commands_created": commands_created,
                    },
                )

                await db.commit()

                return {
                    "status": "success",
                    "event_id": event_log.id,
                    "commands_created": commands_created,
                    "action_params": action_params,
                }

        result = _run_async(_process())

        logger.info(f"[Celery] 设备事件处理成功: {result}")
        return result

    except Exception as e:
        logger.error(f"[Celery] 处理设备事件失败: {e}", exc_info=True)

        # 记录错误到事件日志
        try:

            async def _log_error(exc: Exception) -> None:
                from src.app.device.models.event_log import EventRequest, EventType
                from src.app.device.services import device_command_service

                async with self.db as db:
                    # 错误处理中仍然需要验证必需字段
                    device_code_err = event_data.get("device_code")
                    event_type_str = event_data.get("event_type")
                    timestamp_err = event_data.get("timestamp")
                    data_err = event_data.get("data")

                    if not all([device_code_err, event_type_str]):
                        logger.error("错误日志：缺少必需字段，跳过记录")
                        return

                    event_request = EventRequest(
                        device_code=cast("str", device_code_err),
                        event_type=EventType(event_type_str),
                        timestamp=timestamp_err,
                        data=data_err,
                    )
                    event_log = await device_command_service.create_event_log(db, event_request)
                    _ = await device_command_service.update_event_log(
                        db,
                        event_log,
                        processed=True,
                        error_message=str(exc),
                    )
                    await db.commit()

            _run_async(_log_error(e))
        except Exception as log_error:
            logger.error(f"记录错误日志失败: {log_error}")

        # 自动重试（指数退避）
        countdown = 5 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


# ============================================
# 兼容性任务（保留旧接口）
# ============================================


@celery_app.task(
    name="src.celery_app.tasks.device.process_material_arrived",
    base=DeviceTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_material_arrived(self: DeviceTask, event_data: dict[str, Any]) -> Any:
    """处理料盘到达事件（兼容性任务）"""
    from src.utils.timezone import timezone

    # 转换为新格式
    new_event_data: dict[str, Any] = {
        "device_code": event_data.get("device_code"),
        "event_type": "MATERIAL_ARRIVED",
        "timestamp": int(timezone.now_utc().timestamp() * 1000),
        "data": {
            "barcode": event_data.get("barcode"),
            "location": event_data.get("location"),
        },
    }

    # 调用新的统一处理任务
    return cast("Any", process_device_event).apply_async(args=[new_event_data]).get()


@celery_app.task(
    name="src.celery_app.tasks.device.process_scan_completed",
    base=DeviceTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_scan_completed(self: DeviceTask, event_data: dict[str, Any]) -> Any:
    """处理扫码完成事件（兼容性任务）"""
    from src.utils.timezone import timezone

    new_event_data: dict[str, Any] = {
        "device_code": event_data.get("device_code"),
        "event_type": "SCAN_COMPLETED",
        "timestamp": int(timezone.now_utc().timestamp() * 1000),
        "data": {
            "barcode": event_data.get("barcode"),
            "location": event_data.get("location"),
        },
    }

    return cast("Any", process_device_event).apply_async(args=[new_event_data]).get()


# ============================================
# 导出
# ============================================

__all__ = [
    "DeviceTask",
    "_run_async",
    "process_device_event",
    "process_material_arrived",
    "process_scan_completed",
]
