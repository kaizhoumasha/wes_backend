"""
作业线编排 Celery 任务

本文件提供 Workline 核心流程的 Celery 任务入口。
核心业务逻辑（如 Inbox 批量处理、Orchestrator 写回、出站下发等）
已抽离至 `src/app/workline/services/` 目录下。
设计参考: 设计文档 phase2-orchestrator
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable

from celery import Task

# 预加载外键目标模型，确保独立 Celery worker 进程内 mapper/metadata 完整注册。
from src.app.device.models.command import DeviceCommand
from src.celery_app.app import celery_app
from src.celery_app.constants import (
    DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
)
from src.core.logger import logger
from src.database import db as db_module
from src.utils.value_normalization import (
    enum_value,
    resolve_entity_id,
)
from src.workline_runtime.diagnostics import ErrorCode


# ============================================
# 类型定义
# ============================================
class ProcessResult(TypedDict):
    """处理结果"""

    processed: int
    success: int
    failed: int
    skipped: int
    resource_wait: int


class ScanResult(TypedDict):
    """扫描结果"""

    scanned: int
    timeouts_created: int
    ack_timeouts_reconciled: int
    errors: int


class DeviceHeartbeatScanResult(TypedDict):
    """设备心跳扫描结果"""

    scanned: int
    marked_offline: int


class DispatchResult(TypedDict):
    """派发结果"""

    dispatched: int
    success: int
    failed: int
    skipped: int


_WORKLINE_TASK_LOOP: asyncio.AbstractEventLoop | None = None


def _ensure_non_empty_retry_result(task_name: str, result: dict[str, int], retries: int) -> None:
    """避免“重试后空跑”被 Celery 误记为成功。"""
    if retries <= 0:
        return
    if any(value > 0 for value in result.values()):
        return
    raise RuntimeError(
        f"{task_name} returned an empty result after {retries} retries; refusing to mark it as succeeded"
    )


def _get_sync_event_loop() -> asyncio.AbstractEventLoop:
    global _WORKLINE_TASK_LOOP
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        if _WORKLINE_TASK_LOOP is None or _WORKLINE_TASK_LOOP.is_closed():
            _WORKLINE_TASK_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_WORKLINE_TASK_LOOP)
        return _WORKLINE_TASK_LOOP
    raise RuntimeError("当前事件循环正在运行，无法同步执行 Workline Celery 任务")


def _lazy_init_db(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """在直接调用或 Worker 子进程未完成 signal 初始化时懒初始化数据库。"""
    if db_module.AsyncSessionLocal is not None:
        return
    from src.database.db import init_db

    init_loop = loop or _get_sync_event_loop()
    init_loop.run_until_complete(init_db())
    logger.info("✓ Workline Celery 任务懒初始化数据库连接成功")


def _run_async(coro: Awaitable[Any]) -> Any:
    """在 Celery 同步任务中运行异步函数。"""
    try:
        loop = _get_sync_event_loop()
        _lazy_init_db(loop)
    except Exception:
        with suppress(Exception):
            cast("Any", coro).close()
        raise
    return loop.run_until_complete(coro)


async def _record_process_inbox_batch_failure_diagnostic(
    db: Any,
    *,
    exc: Exception,
    limit: int,
    retries: int,
    max_retries: int,
    task_id: str | None,
) -> None:
    """记录无法归属到单条 Inbox 的批处理级失败。"""
    from src.app.workline.diagnostic_support import _record_diagnostic

    task_name = "src.celery_app.tasks.workline.process_inbox_batch"
    trace_id = f"celery:{task_name}"
    await _record_diagnostic(
        db,
        inbox=None,
        error_code=ErrorCode.INBOX_RETRY_EXHAUSTED,
        message=f"Inbox batch processing exhausted retries: {exc}",
        request_id=task_id,
        trace_id=trace_id,
        extra={
            "task_name": task_name,
            "task_id": task_id,
            "limit": limit,
            "retries": retries,
            "max_retries": max_retries,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        },
    )
    await db.commit()


# ============================================
# Celery 任务
# ============================================
class WorklineTask(Task):
    """作业线任务基类 - 提供数据库会话管理"""

    def __init__(self) -> None:
        super().__init__()
        self._db: Any | None = None

    @property
    def db(self) -> Any:
        """懒加载数据库会话"""
        if self._db is None:
            _lazy_init_db()
            session_local = db_module.AsyncSessionLocal
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        """清理资源"""
        if self._db:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._db.close())
            except Exception as exc:
                logger.warning(f"关闭任务数据库会话失败: {exc}")
            finally:
                loop.close()
            self._db = None

    def on_failure(
        self, exc: Exception, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any], einfo: Any
    ) -> None:
        """任务失败时清理资源"""
        _ = args, kwargs, einfo
        self.cleanup()
        logger.error(f"任务 {task_id} 失败: {exc}")

    def on_success(self, retval: Any, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """任务成功时清理资源"""
        _ = retval, args, kwargs
        self.cleanup()
        logger.info(f"任务 {task_id} 成功完成")


class TimeoutScanner:
    """系统 timeout inbox 扫描器内部类
    职责：
    - 扫描 ACK 后执行等待超时的 Session（deadline_at < now）
    - 为超时 Session 幂等创建系统 TIMER_TIMEOUT Inbox
    - 后续由 runtime reconciliation handler 处理，不进入插件 timeout 编排
    调用方式：
    - 通过 Celery 任务 scan_timeouts_batch 间接调用
    - 可直接调用 _scan() 进行单元测试
    """

    @staticmethod
    async def _scan(db: Any, limit: int = 100) -> ScanResult:
        """扫描超时 Session 并创建 Timeout Inbox
        处理流程：
        1. 查询 deadline_at < NOW() 的超时 Session
        2. 遍历每个超时会话：
           a. 创建 type='timeout' 的 Inbox 消息
           b. 继承原 Session 的 trace_id
        3. 提交数据库事务
        Args:
            db: 数据库会话
            limit: 批处理数量，默认 100
        Returns:
            扫描结果统计 {
                "scanned": 扫描的 Session 数,
                "timeouts_created": 创建的超时 Inbox 数,
                "ack_timeouts_reconciled": ACK 前超时并进入对账的指令数,
                "errors": 错误数
            }
        """
        from src.app.workline.repositories.session_repository import (
            WorklineSessionRepository,
        )
        from src.app.workline.services.inbox_service import inbox_service
        from src.app.workline.services.runtime_reconciliation_service import workline_runtime_reconciliation_service

        result: ScanResult = {
            "scanned": 0,
            "timeouts_created": 0,
            "ack_timeouts_reconciled": 0,
            "errors": 0,
        }
        from src.app.device.repositories.command_repository import DeviceCommandRepository
        from src.app.device.repositories.device_repository import device_repository
        from src.app.sys.repositories import SystemOutboxRepository

        # 获取 ACK 后执行等待超时 Session
        session_repo = WorklineSessionRepository()
        sessions = await session_repo.get_timed_out_sessions(db, limit=limit)
        result["scanned"] = len(sessions)
        for session in sessions:
            try:
                session_pk = resolve_entity_id(session)
                if session_pk is None:
                    raise ValueError("Timed out session missing primary key")
                awaiting_command_id = getattr(session, "awaiting_command_id", None)
                command = (
                    await db.get(DeviceCommand, awaiting_command_id) if isinstance(awaiting_command_id, int) else None
                )
                if enum_value(getattr(session, "status", None)) == "WAITING_DEVICE_RESULT" and command is None:
                    raise ValueError(f"Timed out session awaiting command missing: session_id={session_pk}")
                command_device_id = getattr(command, "device_id", None)
                device = await device_repository.get_by_id(db, command_device_id) if command_device_id else None
                # 幂等创建系统 timeout Inbox
                _ = await inbox_service.create_timeout_inbox(
                    db=db,
                    session_id=session_pk,
                    workline_id=session.workline_id,
                    deadline_at=session.deadline_at,
                    trace_id=session.trace_id,
                    wait_token=getattr(command, "command_code", None),
                    wait_type=getattr(session, "current_wait_type", None),
                    awaiting_command_id=awaiting_command_id,
                    command_code=getattr(command, "command_code", None),
                    device_id=command_device_id,
                    device_code=getattr(device, "device_code", None),
                    command_status=enum_value(getattr(command, "status", None)) if command is not None else None,
                    ack_received_at=getattr(command, "ack_received_at", None),
                    auto_commit=False,
                )
                result["timeouts_created"] += 1
                logger.info(f"Session {session_pk} 超时，已创建 Timeout Inbox")
            except Exception as e:
                session_pk = resolve_entity_id(session)
                logger.error(f"Session {session_pk or 'unknown'} 创建超时 Inbox 失败: {e}")
                result["errors"] += 1
        # 获取 ACK 前通信等待超时 Command：设备已经接收出站指令派发，但一直没有 ACK。
        command_repo = DeviceCommandRepository()
        outbox_repo = SystemOutboxRepository()
        ack_timeout_commands = await command_repo.get_ack_timed_out_commands(db, limit=limit)
        result["scanned"] += len(ack_timeout_commands)
        for command in ack_timeout_commands:
            command_code = getattr(command, "command_code", None)
            try:
                if not isinstance(command_code, str) or not command_code:
                    raise ValueError("ACK timed out command missing command_code")
                outbox = await outbox_repo.get_by_dispatch_key(db, f"device-command:{command_code}")
                if outbox is None:
                    raise ValueError(f"ACK timed out command outbox missing: command_code={command_code}")
                session = await workline_runtime_reconciliation_service.handle_dispatch_ack_exhausted(
                    db,
                    outbox=outbox,
                    command=command,
                    error_message="COMMAND_ACK_TIMEOUT",
                )
                if session is not None:
                    result["ack_timeouts_reconciled"] += 1
                    logger.info(f"Command {command_code} ACK 等待超时，已进入 runtime reconciliation")
            except Exception as e:
                logger.error(f"Command {command_code or 'unknown'} ACK 等待超时处理失败: {e}")
                result["errors"] += 1
        # 提交事务
        await db.commit()
        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result


class DeviceHeartbeatScanner:
    """设备心跳超时扫描器。
    设备任务状态仍由 DeviceCommand 记录；这里仅维护设备健康/占用投影，
    将心跳超时的 IDLE/RUNNING 设备标记为 OFFLINE。
    """

    @staticmethod
    async def _scan(
        db: Any,
        *,
        threshold_seconds: int = DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
        limit: int = 100,
    ) -> DeviceHeartbeatScanResult:
        from src.app.device.services import device_service

        marked_offline = await device_service.mark_stale_heartbeats_offline(
            db,
            threshold_seconds=threshold_seconds,
            limit=limit,
            auto_commit=False,
        )
        await db.commit()
        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return {
            "scanned": marked_offline,
            "marked_offline": marked_offline,
        }


@celery_app.task(
    name="src.celery_app.tasks.workline.process_inbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_inbox_batch(self: WorklineTask, limit: int = 10) -> ProcessResult:
    """顺序处理 Inbox 消息 (Celery 任务入口)
    通过 InboxBatchProcessor.process_batch() claim 并处理消息。
    处理流程（详见 InboxBatchProcessor）：
    1. 每轮 claim 1 条待处理消息（总量受 limit 限制）
    2. token fencing：标记为 PROCESSING，防止重复处理
    3. 入站后 malformed gate：拦截完全空的 SCAN_COMPLETED payload
    4. 加载关联实体：session/workline/device/devices_by_role
    5. 调用 OrchestratorService 执行编排
    6. 应用编排结果：command/outbox/timeline
    7. 更新状态：PROCESSED/FAILED/DEAD_LETTER/RETRY(RESOURCE_WAIT)
    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=5：重试间隔 5 秒（指数退避）
    调用链：
        process_inbox_batch() → InboxBatchProcessor.process_batch()
    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 10
    Returns:
        处理结果统计 {
            "processed": 处理总数,
            "success": 成功数,
            "failed": 失败数,
            "skipped": 跳过数,
            "resource_wait": 资源暂忙等待数
        }
    触发方式：
        celery beat 定时调度（默认每 5 秒）
        手动调用：process_inbox_batch.delay(limit=10)
    """
    logger.debug(f"开始处理 Inbox 消息, limit={limit}")

    async def _process() -> ProcessResult:
        async with self.db as db:
            from src.app.workline.services.inbox_batch_processor import InboxBatchProcessor
            from src.app.workline.services.write_back_service import orchestrator_write_back_service

            processor = InboxBatchProcessor(write_back_service=orchestrator_write_back_service)
            return await processor.process_batch(db, limit=limit)

    try:
        result = _run_async(_process())
        _ensure_non_empty_retry_result(
            "process_inbox_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        if result.get("processed", 0) > 0:
            logger.info(f"Inbox 处理完成: {result}")
        else:
            logger.debug(f"Inbox 处理完成: {result}")
        return result
    except Exception as e:
        logger.error(f"Inbox 处理失败: {e}")
        retries = int(getattr(self.request, "retries", 0) or 0)
        max_retries = int(getattr(self, "max_retries", 0) or 0)
        if max_retries and retries >= max_retries:
            try:
                _run_async(
                    _record_process_inbox_batch_failure_diagnostic(
                        self.db,
                        exc=e,
                        limit=limit,
                        retries=retries,
                        max_retries=max_retries,
                        task_id=getattr(self.request, "id", None),
                    )
                )
            except Exception as diagnostic_error:
                logger.warning(f"Inbox 批处理失败诊断补记失败: {diagnostic_error}")
        countdown = 5 * (2**retries)
        raise self.retry(exc=e, countdown=countdown) from None


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_timeouts_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_timeouts_batch(self: WorklineTask, limit: int = 100) -> ScanResult:
    """扫描超时 Session (Celery 任务入口)
    扫描 deadline_at < NOW() 的超时 Session，为每个超时 Session 创建 timeout 类型的 Inbox 消息，
    触发后续编排流程处理超时。
    处理流程（详见 TimeoutScanner）：
    1. 查询 deadline_at < NOW() 的超时 Session
    2. 遍历每个超时会话：
       a. 创建 type='TIMEOUT' 的 Inbox 消息
       b. 继承原 Session 的 trace_id
    3. 提交数据库事务
    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=60：重试间隔 60 秒（超时场景不频繁）
    调用链：
        scan_timeouts_batch() → TimeoutScanner._scan()
    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 100
    Returns:
        扫描结果统计 {
            "scanned": 扫描的 Session 数,
            "timeouts_created": 创建的超时 Inbox 数,
            "ack_timeouts_reconciled": ACK 前超时并进入对账的指令数,
            "errors": 错误数
        }
    触发方式：
        celery beat 定时调度（默认每 30 秒）
        手动调用：scan_timeouts_batch.delay(limit=100)
    注意：
        - 使用幂等性键防止重复创建 timeout Inbox
        - 创建的 Inbox 类型为 InboxKind.TIMER_TIMEOUT
    """
    logger.info(f"开始扫描超时 Session, limit={limit}")

    async def _scan() -> ScanResult:
        async with self.db as db:
            return await TimeoutScanner._scan(db, limit=limit)

    try:
        result = _run_async(_scan())
        _ensure_non_empty_retry_result(
            "scan_timeouts_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(f"超时扫描完成: {result}")
        return result
    except Exception as e:
        logger.error(f"超时扫描失败: {e}")
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_device_heartbeats_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_device_heartbeats_batch(
    self: WorklineTask,
    threshold_seconds: int = DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
    limit: int = 100,
) -> DeviceHeartbeatScanResult:
    """扫描设备心跳超时，将已有心跳且超时的设备标记为 OFFLINE。"""
    logger.info(f"开始扫描设备心跳超时, threshold_seconds={threshold_seconds}, limit={limit}")

    async def _scan() -> DeviceHeartbeatScanResult:
        async with self.db as db:
            return await DeviceHeartbeatScanner._scan(db, threshold_seconds=threshold_seconds, limit=limit)

    try:
        result = _run_async(_scan())
        _ensure_non_empty_retry_result(
            "scan_device_heartbeats_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(f"设备心跳扫描完成: {result}")
        return result
    except Exception as e:
        logger.error(f"设备心跳扫描失败: {e}")
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


# ============================================
# 导出
# ============================================
__all__ = [
    # Celery 任务入口（公共 API）
    "process_inbox_batch",
    "process_signal",
    "scan_device_heartbeats_batch",
    "scan_timeouts_batch",
]


@celery_app.task(
    name="src.celery_app.tasks.workline.process_signal",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: WorklineTask, payload: dict[str, Any]) -> None:
    logger.info(f"workline process_signal 接收到 payload: {payload}")
