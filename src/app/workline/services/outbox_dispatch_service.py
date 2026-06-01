from inspect import isawaitable
from typing import Any, TypedDict

from src.app.device.models import DeviceStatus
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.outbox_dispatch_support import (
    _outbox_trace_extra,
    _outbox_trace_log_suffix,
    _resolve_outbox_run_mode,
)
from src.app.workline.services.device_command_gateway import (
    _build_device_command_log_envelope,
    _DeviceCommandGovernanceError,
    _is_same_session_current_command,
    _mark_device_command_failed_if_dispatch_exhausted,
    _mark_outbox_blocked_by_workline_state,
)
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.core.logger import logger
from src.utils.value_normalization import (
    enum_value,
    resolve_entity_id,
    resolve_required_pk,
    string_value,
)
from src.workline_runtime.diagnostics.codes import ErrorCode
from src.workline_runtime.run_mode import is_simulation_run_mode
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.utils import payload_dict


class DispatchResult(TypedDict):
    dispatched: int
    success: int
    failed: int
    skipped: int


def _count_workline_safety_block_result(result: DispatchResult, block_state: str) -> None:
    if block_state == "blocked_resource":
        result["skipped"] += 1
    else:
        result["failed"] += 1
    result["dispatched"] += 1


def _dispatch_failure_diagnostic_code(failed_outbox: Any) -> ErrorCode:
    if enum_value(getattr(failed_outbox, "status", None)) == "FAILED":
        return ErrorCode.OUTBOX_DISPATCH_FAILED
    return ErrorCode.OUTBOX_ACK_TIMEOUT


async def _block_outbox_for_workline_safety(
    db: Any,
    *,
    outbox_repo: Any,
    outbox: Any,
    outbox_id: int,
    safety_error: WorkLineSafetyBlocked,
    trace: TraceContext,
    dispatch_attempt: Any | None = None,
    attempt_service: Any | None = None,
    final_guard: bool = False,
) -> str:
    await _record_diagnostic(
        db,
        inbox=None,
        outbox=outbox,
        error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
        message=str(safety_error),
        extra=_outbox_trace_extra(outbox, trace=trace),
    )
    if dispatch_attempt is not None and attempt_service is not None:
        _ = await attempt_service.finalize_attempt_record(
            db,
            attempt=dispatch_attempt,
            success=False,
            error_message=str(safety_error),
            auto_commit=False,
        )
    block_state = await _mark_outbox_blocked_by_workline_state(
        db,
        outbox_repo=outbox_repo,
        outbox=outbox,
        outbox_id=outbox_id,
        safety_error=safety_error,
    )
    await db.commit()
    guard_label = "最终安全状态" if final_guard else "安全状态"
    logger.warning(
        f"Outbox {outbox_id} 因 WorkLine {guard_label}阻断派发 ({_outbox_trace_log_suffix(outbox, trace=trace)})"
    )
    return block_state


async def _block_outbox_for_device_busy(
    db: Any,
    *,
    outbox_repo: Any,
    outbox: Any,
    outbox_id: int,
    governance_error: _DeviceCommandGovernanceError,
    dispatch_attempt: Any | None = None,
    attempt_service: Any | None = None,
) -> bool:
    """目标设备仍忙时，暂停 outbox 并等待设备释放后重派。"""
    if governance_error.code != "DEVICE_BUSY":
        raise governance_error
    if dispatch_attempt is not None and attempt_service is not None:
        _ = await attempt_service.finalize_attempt_record(
            db,
            attempt=dispatch_attempt,
            success=False,
            error_message=governance_error.message,
            response={"result": "blocked_resource", "reason": governance_error.code},
            auto_commit=False,
        )
    blocked_outbox = await outbox_repo.mark_as_blocked_by_device_busy(
        db,
        outbox_id,
        blocked_device_id=governance_error.device_id,
        blocked_workline_id=getattr(outbox, "workline_id", None),
        reason=governance_error.code,
        last_error=governance_error.message,
    )
    await db.commit()
    if blocked_outbox is None:
        logger.warning(
            f"Outbox {outbox_id} 因设备忙暂停时被 fencing 拒绝，保留当前状态 ({_outbox_trace_log_suffix(outbox)})"
        )
        return False
    logger.info(f"Outbox {outbox_id} 因设备 {governance_error.device_code or governance_error.device_id} 忙暂停派发")
    return True


def _latest_dispatch_attempt(attempts: list[Any]) -> Any | None:
    if not attempts:
        return None
    return max(attempts, key=lambda item: getattr(item, "attempt_no", 0) or 0)


def _is_device_busy_attempt(attempt: Any | None) -> bool:
    if attempt is None:
        return False
    if enum_value(getattr(attempt, "status", None)) != "FAILED":
        return False
    response = payload_dict(getattr(attempt, "response_json", None))
    reason = string_value(response.get("reason"))
    if reason == "DEVICE_BUSY":
        return True
    error_message = string_value(getattr(attempt, "error_message", None))
    return "DEVICE_BUSY" in error_message


def _is_device_idle_for_requeue(device: Any | None) -> bool:
    if device is None:
        return False
    return (
        enum_value(getattr(device, "device_status", None)) == DeviceStatus.IDLE.value
        and getattr(device, "current_command_id", None) is None
    )


def _is_dispatched_command(command: Any | None) -> bool:
    return enum_value(getattr(command, "status", None)) in {"SENT", "ACK_RECEIVED"}


async def _repair_orphaned_device_busy_dispatches(db: Any, *, outbox_repo: Any, limit: int) -> int:
    """恢复 attempt 已结束但 outbox 仍卡在 DISPATCHING 的设备忙派发。"""
    from src.app.device.repositories.device_repository import device_repository
    from src.app.workline.repositories.dispatch_attempt_repository import workline_dispatch_attempt_repository

    getter = getattr(outbox_repo, "get_dispatching_device_messages", None)
    if not callable(getter):
        return 0
    repaired = 0
    dispatching_messages = getter(db, limit=limit)
    if not isawaitable(dispatching_messages):
        return 0
    for outbox in await dispatching_messages:
        outbox_id = resolve_entity_id(outbox)
        if outbox_id is None:
            continue
        attempts = await workline_dispatch_attempt_repository.get_by_outbox_id(db, outbox_id)
        latest_attempt = _latest_dispatch_attempt(attempts)
        if not _is_device_busy_attempt(latest_attempt):
            continue
        target_code = string_value(getattr(outbox, "target_code", None))
        if not target_code:
            continue
        device = await device_repository.get_by_device_code(db, target_code)
        device_id = resolve_entity_id(device)
        if device_id is None:
            continue
        error_message = string_value(getattr(latest_attempt, "error_message", None), default="DEVICE_BUSY")
        blocked = await outbox_repo.mark_as_blocked_by_device_busy(
            db,
            outbox_id,
            blocked_device_id=device_id,
            blocked_workline_id=getattr(outbox, "workline_id", None),
            reason="DEVICE_BUSY",
            last_error=error_message,
        )
        if blocked is None:
            continue
        repaired += 1
        if _is_device_idle_for_requeue(device):
            _ = await outbox_repo.release_blocked_by_device(
                db,
                device_id=device_id,
                workline_id=getattr(outbox, "workline_id", None),
            )
        await db.commit()
    if repaired:
        logger.info(f"已恢复 {repaired} 条设备忙残留派发 outbox")
    return repaired


async def _repair_self_blocked_device_busy_dispatches(db: Any, *, outbox_repo: Any, limit: int) -> int:
    """恢复同一命令已占用设备运行态却被误标为 DEVICE_BUSY 的 outbox。"""
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.device.repositories.device_repository import device_repository

    getter = getattr(outbox_repo, "get_blocked_device_busy_messages", None)
    if not callable(getter):
        return 0
    blocked_messages = getter(db, limit=limit)
    if not isawaitable(blocked_messages):
        return 0
    repaired = 0
    command_repo = DeviceCommandRepository()
    for outbox in await blocked_messages:
        outbox_id = resolve_entity_id(outbox)
        if outbox_id is None:
            continue
        payload = payload_dict(getattr(outbox, "payload_json", None))
        command_code = string_value(payload.get("command_code"))
        if not command_code:
            continue
        command = await command_repo.get_by_command_code(db, command_code)
        target_code = string_value(getattr(outbox, "target_code", None))
        if not target_code:
            continue
        device = await device_repository.get_by_device_code(db, target_code)
        if not _is_dispatched_command(command):
            continue
        if not _is_same_session_current_command(outbox=outbox, command=command, device=device):
            continue
        marked = await outbox_repo.mark_blocked_device_busy_as_sent(db, outbox_id)
        if marked is None:
            continue
        repaired += 1
        await db.commit()
    if repaired:
        logger.info(f"已恢复 {repaired} 条同命令自阻塞 DEVICE_BUSY outbox")
    return repaired


class OutboxDispatchService:
    MAX_RETRIES = 3

    async def dispatch(self, db: Any, limit: int = 50) -> DispatchResult:  # noqa: PLR0912
        """派发 Outbox 消息
        Args:
            db: 数据库会话
            limit: 批处理数量
        Returns:
            派发结果统计
        """
        from src.app.sys.repositories import SystemOutboxRepository
        from src.app.workline.services.dispatch_attempt_service import workline_dispatch_attempt_service
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked, workline_safety_service

        result: DispatchResult = {
            "dispatched": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }
        # 获取待派发消息
        outbox_repo = SystemOutboxRepository()
        _ = await _repair_orphaned_device_busy_dispatches(db, outbox_repo=outbox_repo, limit=limit)
        _ = await _repair_self_blocked_device_busy_dispatches(db, outbox_repo=outbox_repo, limit=limit)
        messages = await outbox_repo.get_pending_messages(db, limit=limit, operation_domains=("WORKLINE", "RACK"))
        for outbox in messages:
            outbox_pk_text = str(getattr(outbox, "id", "unknown"))
            trace = TraceContext.from_runtime(outbox=outbox)
            dispatch_attempt: Any | None = None
            try:
                outbox_pk = resolve_required_pk(outbox, "outbox", "id", "outbox_id")
                outbox_workline_id = getattr(outbox, "workline_id", None)
                if outbox_workline_id is not None:
                    try:
                        await workline_safety_service.assert_accepting_work(db, workline_id=outbox_workline_id)
                    except WorkLineSafetyBlocked as safety_error:
                        block_state = await _block_outbox_for_workline_safety(
                            db,
                            outbox_repo=outbox_repo,
                            outbox=outbox,
                            outbox_id=outbox_pk,
                            safety_error=safety_error,
                            trace=trace,
                        )
                        _count_workline_safety_block_result(result, block_state)
                        continue
                # 尝试标记为派发中（并发控制）。前置 WorkLine 锁已释放前按统一锁序完成。
                updated = await outbox_repo.mark_as_dispatching(db, outbox_pk)
                if updated is None:
                    await db.commit()
                    # 已被其他 worker 处理
                    result["skipped"] += 1
                    continue
                await db.commit()
                if outbox_workline_id is not None:
                    try:
                        await workline_safety_service.assert_accepting_work(db, workline_id=outbox_workline_id)
                    except WorkLineSafetyBlocked as safety_error:
                        block_state = await _block_outbox_for_workline_safety(
                            db,
                            outbox_repo=outbox_repo,
                            outbox=outbox,
                            outbox_id=outbox_pk,
                            safety_error=safety_error,
                            trace=trace,
                        )
                        _count_workline_safety_block_result(result, block_state)
                        continue
                    await db.commit()
                dispatch_attempt = await workline_dispatch_attempt_service.create_attempt(
                    db,
                    outbox=outbox,
                    auto_commit=False,
                )
                await db.commit()
                if outbox_workline_id is not None:
                    try:
                        await workline_safety_service.assert_accepting_work(db, workline_id=outbox_workline_id)
                    except WorkLineSafetyBlocked as safety_error:
                        block_state = await _block_outbox_for_workline_safety(
                            db,
                            outbox_repo=outbox_repo,
                            outbox=outbox,
                            outbox_id=outbox_pk,
                            safety_error=safety_error,
                            trace=trace,
                            dispatch_attempt=dispatch_attempt,
                            attempt_service=workline_dispatch_attempt_service,
                            final_guard=True,
                        )
                        _count_workline_safety_block_result(result, block_state)
                        continue
                    await db.commit()
                # 派发消息
                success = await self._dispatch_single(db, outbox)
                if success:
                    sent_outbox = await outbox_repo.mark_as_sent(db, outbox_pk)
                    if dispatch_attempt is not None:
                        _ = await workline_dispatch_attempt_service.finalize_attempt_record(
                            db,
                            attempt=dispatch_attempt,
                            success=True,
                            response={
                                "result": "sent",
                                "outbox_finalization": "sent" if sent_outbox is not None else "fenced",
                            },
                            auto_commit=False,
                        )
                    if sent_outbox is None:
                        await db.commit()
                        result["skipped"] += 1
                        logger.warning(
                            f"Outbox {outbox_pk} 物理派发成功但终态更新被 fencing 拒绝，保留当前安全状态 "
                            f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                        )
                        result["dispatched"] += 1
                        continue
                    await db.commit()
                    result["success"] += 1
                    logger.info(f"Outbox {outbox_pk} 派发成功 ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                else:
                    trace_extra = _outbox_trace_extra(outbox, trace=trace)
                    failure_reason = string_value(
                        getattr(outbox, "_dispatch_failure_reason", None),
                        default="Dispatch failed",
                    )
                    failure_code = string_value(getattr(outbox, "_dispatch_failure_error_code", None), default="")
                    failed_outbox = await outbox_repo.mark_as_failed(
                        db,
                        outbox_pk,
                        failure_reason,
                        self.MAX_RETRIES,
                    )
                    if dispatch_attempt is not None:
                        _ = await workline_dispatch_attempt_service.finalize_attempt_record(
                            db,
                            attempt=dispatch_attempt,
                            success=False,
                            error_message=failure_reason,
                            auto_commit=False,
                        )
                    if failed_outbox is None:
                        await db.commit()
                        result["skipped"] += 1
                        logger.warning(
                            f"Outbox {outbox_pk} 物理派发失败但失败更新被 fencing 拒绝，保留当前安全状态 "
                            f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                        )
                        result["dispatched"] += 1
                        continue
                    await _record_diagnostic(
                        db,
                        inbox=None,
                        outbox=outbox,
                        error_code=(
                            ErrorCode.OUTBOX_DISPATCH_FAILED
                            if failure_code == "DEVICE_STATUS_PRECHECK_FAILED"
                            else _dispatch_failure_diagnostic_code(failed_outbox)
                        ),
                        message=failure_reason,
                        extra=trace_extra,
                    )
                    if failure_code != "DEVICE_STATUS_PRECHECK_FAILED":
                        await _mark_device_command_failed_if_dispatch_exhausted(
                            db,
                            outbox=outbox,
                            failed_outbox=failed_outbox,
                            error_message=failure_reason,
                        )
                    await db.commit()
                    result["failed"] += 1
                    logger.warning(f"Outbox {outbox_pk} 派发失败 ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                result["dispatched"] += 1
            except _DeviceCommandGovernanceError as e:
                outbox_pk = resolve_entity_id(outbox)
                if e.code == "DEVICE_BUSY":
                    logger.info(
                        f"Outbox {outbox_pk_text} 等待目标设备空闲: {e.message} "
                        f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                    )
                    try:
                        if outbox_pk is not None:
                            _ = await _block_outbox_for_device_busy(
                                db,
                                outbox_repo=outbox_repo,
                                outbox=outbox,
                                outbox_id=outbox_pk,
                                governance_error=e,
                                dispatch_attempt=dispatch_attempt,
                                attempt_service=workline_dispatch_attempt_service,
                            )
                        else:
                            await db.commit()
                    except Exception as mark_error:
                        logger.warning(f"Outbox {outbox_pk_text} 设备忙暂停补记失败: {mark_error}")
                        result["failed"] += 1
                        result["dispatched"] += 1
                        continue
                    result["skipped"] += 1
                    result["dispatched"] += 1
                    continue
                logger.warning(
                    f"Outbox {outbox_pk_text} 命令治理拒绝: {e.message} "
                    f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                )
                if dispatch_attempt is not None:
                    try:
                        _ = await workline_dispatch_attempt_service.finalize_attempt_record(
                            db,
                            attempt=dispatch_attempt,
                            success=False,
                            error_message=e.message,
                            response={"result": "failed", "reason": e.code},
                            auto_commit=False,
                        )
                    except Exception as attempt_error:
                        logger.warning(f"Outbox {outbox_pk_text} 派发尝试账本补记失败: {attempt_error}")
                if outbox_pk is not None:
                    _ = await outbox_repo.mark_as_failed(
                        db,
                        outbox_pk,
                        e.message,
                        self.MAX_RETRIES,
                    )
                    await _record_diagnostic(
                        db,
                        inbox=None,
                        outbox=outbox,
                        error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
                        message=e.message,
                        extra=_outbox_trace_extra(outbox, trace=trace),
                    )
                await db.commit()
                result["failed"] += 1
                result["dispatched"] += 1
            except Exception as e:
                logger.exception(f"Outbox {outbox_pk_text} 派发异常 ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                if dispatch_attempt is not None:
                    try:
                        _ = await workline_dispatch_attempt_service.finalize_attempt_record(
                            db,
                            attempt=dispatch_attempt,
                            success=False,
                            error_message=str(e),
                            auto_commit=False,
                        )
                    except Exception as attempt_error:
                        logger.warning(f"Outbox {outbox_pk_text} 派发尝试账本补记失败: {attempt_error}")
                try:
                    outbox_pk = resolve_entity_id(outbox)
                    if outbox_pk is not None:
                        failed_outbox = await outbox_repo.mark_as_failed(
                            db,
                            outbox_pk,
                            str(e),
                            self.MAX_RETRIES,
                        )
                        if failed_outbox is None:
                            await db.commit()
                            result["skipped"] += 1
                            logger.warning(
                                f"Outbox {outbox_pk} 物理派发异常但失败更新被 fencing 拒绝，保留当前安全状态 "
                                f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                            )
                            result["dispatched"] += 1
                            continue
                        await _record_diagnostic(
                            db,
                            inbox=None,
                            outbox=outbox,
                            error_code=_dispatch_failure_diagnostic_code(failed_outbox),
                            message=str(e),
                            extra=_outbox_trace_extra(outbox, trace=trace),
                        )
                        await _mark_device_command_failed_if_dispatch_exhausted(
                            db,
                            outbox=outbox,
                            failed_outbox=failed_outbox,
                            error_message=str(e),
                        )
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Outbox {outbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["dispatched"] += 1
        # 前面的领取、派发账本和终态更新都按消息提交；这里保留空提交兼容无消息路径。
        await db.commit()
        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result

    async def _dispatch_single(self, db: Any, outbox: Any) -> bool:
        """派发单个 Outbox 消息
        Args:
            db: 数据库会话
            outbox: Outbox 消息
        Returns:
            是否成功
        """
        from src.app.sys.models import SystemOutboxDispatchType

        if await self._should_dispatch_to_sandbox(db, outbox):
            return await self._dispatch_sandbox(db, outbox)
        if outbox.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND:
            from src.app.workline.services.device_command_gateway import device_command_gateway

            return await device_command_gateway.dispatch(db, outbox)
        if outbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP:
            return await self._dispatch_external_http(outbox)
        if outbox.dispatch_type == SystemOutboxDispatchType.INTERNAL_SIGNAL:
            return await self._dispatch_internal_signal(outbox)
        logger.warning(f"未知的派发类型: {outbox.dispatch_type}")
        return False

    async def _should_dispatch_to_sandbox(self, db: Any, outbox: Any) -> bool:
        """判断 Outbox 是否应进入沙箱派发出口。"""
        from src.app.sys.models import SystemOutboxDispatchType

        if outbox.dispatch_type not in {
            SystemOutboxDispatchType.DEVICE_COMMAND,
            SystemOutboxDispatchType.EXTERNAL_HTTP,
        }:
            return False
        run_mode = await _resolve_outbox_run_mode(db, outbox)
        return is_simulation_run_mode(run_mode)

    async def _dispatch_sandbox(self, db: Any, outbox: Any) -> bool:
        """派发到沙箱工作台。
        沙箱不改写 payload；Outbox 标记 SENT 后，由调试人员按原 callback/result 协议手工回传。
        对设备命令，SENT 已代表硬件侧待完成任务，必须同步占用设备运行态，避免沙箱假并发。
        """
        from src.app.sys.models import SystemOutboxDispatchType

        if outbox.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND:
            from src.app.workline.services.device_command_gateway import device_command_gateway

            reserved = await device_command_gateway.reserve_sandbox_command(db, outbox)
            if not reserved:
                return False
            payload = payload_dict(getattr(outbox, "payload_json", None))
            logger.info(f"沙箱设备指令参数: {_build_device_command_log_envelope(outbox, payload)}")
        logger.info(
            "Outbox 沙箱派发完成，等待调试人员手工回调 "
            f"({_outbox_trace_log_suffix(outbox)}, session_id={getattr(outbox, 'session_id', None)})"
        )
        return True

    async def _dispatch_external_http(self, outbox: Any) -> bool:
        """派发外部 HTTP 决策（如过站回传）。"""
        from src.app.sys.services.outbox_delivery import dispatch_external_http
        from src.app.sys.services.outbox_engine import _send_external_http, endpoint_registry

        return await dispatch_external_http(outbox, endpoint_registry, _send_external_http)

    async def _dispatch_internal_signal(self, outbox: Any) -> bool:
        """派发内部微服务解耦信号（如释放货位）。"""
        from src.app.sys.services.outbox_delivery import dispatch_internal_signal

        return await dispatch_internal_signal(outbox)


outbox_dispatch_service = OutboxDispatchService()
