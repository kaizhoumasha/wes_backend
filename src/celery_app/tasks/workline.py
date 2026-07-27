"""
作业线编排 Celery 任务

本文件提供 Workline 核心流程的 Celery 任务入口。
核心业务逻辑（如 Inbox 批量处理、Orchestrator 写回、出站下发等）
已抽离至 `src/app/workline/services/` 目录下。
设计参考: runtime-orchestration 设计文档
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

# 预加载外键目标模型，确保独立 Celery worker 进程内 mapper/metadata 完整注册。
from src.app.device.models.command import DeviceCommand as _DeviceCommand  # noqa: F401
from src.celery_app.app import celery_app
from src.celery_app.async_runtime import run_async
from src.celery_app.constants import (
    DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
)
from src.core.logger import logger
from src.database.db import get_db_context
from src.utils.value_normalization import (
    enum_value,
    resolve_entity_id,
)


# ============================================
# 类型定义
# ============================================
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


class SmtInboundHandoffRecoveryResult(TypedDict):
    """SMT 入库 handoff 恢复扫描结果。"""

    scanned: int
    claimed: int
    advanced: int
    retry_scheduled: int
    manual_hold: int
    recovery_errors: int


class DispatchResult(TypedDict):
    """派发结果"""

    dispatched: int
    success: int
    failed: int
    skipped: int


def _ensure_non_empty_retry_result(task_name: str, result: Mapping[str, object], retries: int) -> None:
    """避免“重试后空跑”被 Celery 误记为成功。"""
    if retries <= 0:
        return
    if any(isinstance(value, int) and value > 0 for value in result.values()):
        return
    raise RuntimeError(
        f"{task_name} returned an empty result after {retries} retries; refusing to mark it as succeeded"
    )


def _empty_smt_inbound_handoff_recovery_result() -> SmtInboundHandoffRecoveryResult:
    return {
        "scanned": 0,
        "claimed": 0,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }


async def _scan_smt_inbound_handoff_demands_in_transaction(
    db: AsyncSession,
    *,
    service: Any,
    scan_limit: int,
    recovery_limit: int,
    claim_limit: int,
    stale_after_seconds: int,
    legacy_limit: int | None,
) -> SmtInboundHandoffRecoveryResult:
    """执行一次 SMT recovery，并在 task-local Session 上提交完整批次。"""

    if legacy_limit is not None:
        recovery_result = await service.scan_smt_inbound_handoff_demands_batch(
            db,
            stale_after_seconds=stale_after_seconds,
            limit=legacy_limit,
        )
    else:
        recovery_result = await service.scan_smt_inbound_handoff_demands_batch(
            db,
            stale_after_seconds=stale_after_seconds,
            scan_limit=scan_limit,
            recovery_limit=recovery_limit,
            claim_limit=claim_limit,
        )
    await db.commit()
    return cast("SmtInboundHandoffRecoveryResult", recovery_result)


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
        from src.app.runtime.orchestration.repositories.session_repository import (
            WorklineSessionRepository,
        )
        from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
            workline_runtime_reconciliation_service,
        )
        from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_service

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
                awaiting_device_command_code = getattr(session, "awaiting_device_command_code", None)
                command = (
                    await DeviceCommandRepository().get_by_command_code(db, awaiting_device_command_code)
                    if isinstance(awaiting_device_command_code, str) and awaiting_device_command_code
                    else None
                )
                if enum_value(getattr(session, "status", None)) == "WAITING_DEVICE_RESULT" and command is None:
                    raise ValueError(f"Timed out session awaiting command missing: session_id={session_pk}")
                command_device_id = getattr(command, "device_id", None)
                device = await device_repository.get_by_id(db, command_device_id) if command_device_id else None
                # 幂等创建系统 RuntimeInbox timeout 消息
                accepted = await runtime_inbox_service.accept_timer_timeout(
                    db,
                    session_id=session_pk,
                    workline_id=session.workline_id,
                    deadline_at=session.deadline_at,
                    trace_id=session.trace_id,
                    wait_token=getattr(command, "command_code", None),
                    wait_type=getattr(session, "current_wait_type", None),
                    awaiting_device_command_code=awaiting_device_command_code,
                    command_code=getattr(command, "command_code", None),
                    device_id=command_device_id,
                    device_code=getattr(device, "device_code", None),
                    command_id=resolve_entity_id(command),
                    command_status=enum_value(getattr(command, "status", None)) if command is not None else None,
                    ack_received_at=getattr(command, "ack_received_at", None),
                    auto_commit=False,
                )
                if accepted.created:
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
    name="src.celery_app.tasks.workline.scan_timeouts_batch",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_timeouts_batch(self: Any, limit: int = 100) -> ScanResult:
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
    - bind=True：任务方法接收 Celery Task 实例
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
        - 创建的 RuntimeInbox kind 为 TIMER_TIMEOUT
    """
    logger.info(f"开始扫描超时 Session, limit={limit}")

    async def _scan() -> ScanResult:
        async with get_db_context() as db:
            return await TimeoutScanner._scan(db, limit=limit)

    try:
        result = run_async(_scan)
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
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_device_heartbeats_batch(
    self: Any,
    threshold_seconds: int = DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
    limit: int = 100,
) -> DeviceHeartbeatScanResult:
    """扫描设备心跳超时，将已有心跳且超时的设备标记为 OFFLINE。"""
    logger.info(f"开始扫描设备心跳超时, threshold_seconds={threshold_seconds}, limit={limit}")

    async def _scan() -> DeviceHeartbeatScanResult:
        async with get_db_context() as db:
            return await DeviceHeartbeatScanner._scan(db, threshold_seconds=threshold_seconds, limit=limit)

    try:
        result = run_async(_scan)
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


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_smt_inbound_handoff_demands_batch(
    self: Any,
    scan_limit: int = 100,
    recovery_limit: int = 100,
    claim_limit: int = 10,
    stale_after_seconds: int = 300,
    limit: int | None = None,
) -> SmtInboundHandoffRecoveryResult:
    """扫描 SMT 入库 handoff 到期 demand、卡住 source item 和 READY claim 兜底。"""

    legacy_limit = limit
    if legacy_limit is not None:
        scan_limit = legacy_limit
        recovery_limit = legacy_limit
        claim_limit = 0
    logger.info(
        "开始扫描 SMT 入库 handoff 恢复项, "
        f"scan_limit={scan_limit}, recovery_limit={recovery_limit}, "
        f"claim_limit={claim_limit}, stale_after_seconds={stale_after_seconds}"
    )

    async def _scan() -> SmtInboundHandoffRecoveryResult:
        async with get_db_context() as db:
            from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
                smt_inbound_handoff_service,
            )

            return await _scan_smt_inbound_handoff_demands_in_transaction(
                db,
                service=smt_inbound_handoff_service,
                scan_limit=scan_limit,
                recovery_limit=recovery_limit,
                claim_limit=claim_limit,
                stale_after_seconds=stale_after_seconds,
                legacy_limit=legacy_limit,
            )

    try:
        result = run_async(_scan)
        _ensure_non_empty_retry_result(
            "scan_smt_inbound_handoff_demands_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        if result != _empty_smt_inbound_handoff_recovery_result():
            logger.info(f"SMT 入库 handoff 恢复扫描完成: {result}")
        else:
            logger.debug(f"SMT 入库 handoff 恢复扫描完成: {result}")
        return result
    except Exception as e:
        logger.error(f"SMT 入库 handoff 恢复扫描失败: {e}")
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


@celery_app.task(
    name="src.celery_app.tasks.workline.check_wms_effect_status",
    base=celery_app.Task,
)
def check_wms_effect_status(dispatch_key: str) -> dict[str, object]:
    """按 dispatch key 执行一次 lease-fenced WMS EFFECT 状态确认。"""

    async def _check() -> dict[str, object]:
        from src.app.runtime.orchestration.services.wms_effect_status_service import (
            wms_effect_status_service,
        )

        async with get_db_context() as db:
            result = await wms_effect_status_service.check_dispatch(db, dispatch_key=dispatch_key)
            return asdict(result)

    return cast("dict[str, object]", run_async(_check))


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_wms_effect_status_batch",
    base=celery_app.Task,
)
def scan_wms_effect_status_batch(limit: int | None = None) -> list[dict[str, object]]:
    """小批量顺序确认到期 WMS EFFECT，Beat 只负责兜底扫描。"""

    from src.core.conf import settings

    batch_limit = int(limit or settings.WES_EFFECT_STATUS_SCAN_BATCH_SIZE)

    async def _scan() -> list[dict[str, object]]:
        from src.app.runtime.orchestration.services.wms_effect_status_service import (
            wms_effect_status_service,
        )

        async with get_db_context() as db:
            results = await wms_effect_status_service.check_due_batch(db, limit=batch_limit)
            return [asdict(item) for item in results]

    return cast("list[dict[str, object]]", run_async(_scan))


# ============================================
# 导出
# ============================================
__all__ = [
    # Celery 任务入口（公共 API）
    "_empty_smt_inbound_handoff_recovery_result",
    "check_wms_effect_status",
    "process_signal",
    "scan_device_heartbeats_batch",
    "scan_smt_inbound_handoff_demands_batch",
    "scan_timeouts_batch",
    "scan_wms_effect_status_batch",
]


@celery_app.task(
    name="src.celery_app.tasks.workline.process_signal",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: Any, payload: dict[str, Any]) -> None:
    logger.info(f"workline process_signal 接收到 payload: {payload}")
