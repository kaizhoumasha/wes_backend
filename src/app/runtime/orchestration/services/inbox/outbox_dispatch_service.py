from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from src.app.sys.external_http_transport import ExternalHttpTransportResult

from src.app.runtime.orchestration.diagnostics import ErrorCode
from src.app.runtime.orchestration.services.device_command_gateway import (
    _build_device_command_log_envelope,
    _DeviceCommandGovernanceError,
    _mark_device_command_failed_if_dispatch_exhausted,
    _mark_outbox_blocked_by_workline_state,
)
from src.app.sys.external_http_evidence import (
    ExternalHttpEvidenceRecoveryError,
    recover_external_http_evidence_failure_unknown,
)
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.domain.run_mode import is_simulation_run_mode
from src.app.workline.outbox_dispatch_support import (
    _outbox_trace_extra,
    _outbox_trace_log_suffix,
    _resolve_outbox_run_mode,
)
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.app.workline.trace_context import TraceContext
from src.app.workline.utils import payload_dict
from src.core.logger import logger
from src.utils.value_normalization import (
    coerce_optional_str,
    enum_value,
    resolve_entity_id,
    resolve_required_pk,
    string_value,
)

RESOURCE_WAIT_PROBE_MIN_INTERVAL_SECONDS = 2
DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS = 120
DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT = 30
_DEVICE_RESOURCE_WAIT_CODES = {"DEVICE_BUSY", "DEVICE_STATUS_PRECHECK_WAIT"}


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


def _outbox_payload_text(payload: dict[str, Any], *field_names: str) -> str | None:
    for field_name in field_names:
        value = coerce_optional_str(payload.get(field_name))
        if value:
            return value
    return None


def _runtime_intent_dispatch_trace_id(outbox: Any, payload: dict[str, Any]) -> str:
    outbox_id = resolve_entity_id(outbox)
    return (
        coerce_optional_str(getattr(outbox, "trace_id", None))
        or _outbox_payload_text(payload, "trace_id")
        or coerce_optional_str(getattr(outbox, "dispatch_key", None))
        or f"outbox:{outbox_id or 'unknown'}"
    )


def _runtime_intent_dispatch_correlation_id(outbox: Any, payload: dict[str, Any]) -> str:
    return (
        _outbox_payload_text(payload, "correlation_id", "execution_correlation_id")
        or coerce_optional_str(getattr(outbox, "operation_key", None))
        or coerce_optional_str(getattr(outbox, "dispatch_key", None))
        or coerce_optional_str(getattr(outbox, "session_id", None))
        or coerce_optional_str(getattr(outbox, "workline_id", None))
        or coerce_optional_str(getattr(outbox, "device_id", None))
        or f"outbox:{resolve_entity_id(outbox) or 'unknown'}"
    )


def _runtime_intent_dispatch_provider_code(outbox: Any, payload: dict[str, Any]) -> str:
    dispatch_type = enum_value(getattr(outbox, "dispatch_type", None))
    if dispatch_type == "DEVICE_COMMAND":
        return _outbox_payload_text(payload, "provider_code", "source_system", "provider") or "ECS"
    return (
        _outbox_payload_text(payload, "provider_code", "source_system", "provider")
        or coerce_optional_str(getattr(outbox, "target_code", None))
        or coerce_optional_str(getattr(outbox, "operation_domain", None))
        or "SYSTEM"
    )


def _runtime_intent_dispatch_operation_kind(outbox: Any, payload: dict[str, Any]) -> str:
    return (
        _outbox_payload_text(payload, "operation_kind", "operation_type", "action")
        or coerce_optional_str(enum_value(getattr(outbox, "dispatch_type", None)))
        or "UNKNOWN"
    )


def _emit_runtime_intent_dispatch_observability(outbox: Any) -> None:
    """发出 RuntimeIntent/Outbox 派发观测事件；观测失败不改变派发业务状态。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    payload = payload_dict(getattr(outbox, "payload_json", None))
    try:
        _ = runtime_observability_registry.emit(
            "runtime_intent.dispatch",
            {
                "trace_id": _runtime_intent_dispatch_trace_id(outbox, payload),
                "correlation_id": _runtime_intent_dispatch_correlation_id(outbox, payload),
                "provider_code": _runtime_intent_dispatch_provider_code(outbox, payload),
                "operation_kind": _runtime_intent_dispatch_operation_kind(outbox, payload),
            },
        )
    except Exception as exc:  # pragma: no cover - 防止观测链路反向影响 outbox 派发
        outbox_id = resolve_entity_id(outbox)
        logger.warning(f"RuntimeIntent dispatch 观测事件发射失败: outbox_id={outbox_id or 'UNKNOWN'}, error={exc}")


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


def _resource_wait_detail(governance_error: _DeviceCommandGovernanceError) -> dict[str, Any]:
    detail = dict(governance_error.detail or {})
    if governance_error.code == "DEVICE_BUSY":
        detail.setdefault("last_probe_result", "BUSY")
    if governance_error.code == "DEVICE_STATUS_PRECHECK_WAIT":
        detail.setdefault("last_probe_result", "STATUS_WAIT")
        detail.setdefault("ttl_seconds", DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS)
        detail.setdefault("max_check_count", DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT)
    return detail


def _resource_wait_diagnostic_key(outbox_id: int, reason: str) -> str:
    return f"outbox-resource-wait:{outbox_id}:{reason}"


def _resource_wait_elapsed_seconds(blocked_outbox: Any) -> float:
    from src.utils.timezone import timezone as timezone_utils

    blocked_at = getattr(blocked_outbox, "blocked_at", None)
    if blocked_at is None:
        return 0.0
    return max((timezone_utils.now_for_db() - blocked_at).total_seconds(), 0.0)


def _is_status_precheck_wait_over_ttl(blocked_outbox: Any) -> bool:
    if string_value(getattr(blocked_outbox, "blocked_reason", None)) != "DEVICE_STATUS_PRECHECK_WAIT":
        return False
    elapsed_seconds = _resource_wait_elapsed_seconds(blocked_outbox)
    check_count = getattr(blocked_outbox, "blocked_check_count", 0) or 0
    return (
        elapsed_seconds >= DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS
        or check_count >= DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT
    )


async def _escalate_status_precheck_wait_if_needed(
    db: Any,
    *,
    outbox_repo: Any,
    outbox: Any,
    outbox_id: int,
    blocked_outbox: Any,
    message: str,
) -> None:
    if not _is_status_precheck_wait_over_ttl(blocked_outbox):
        return
    detail = payload_dict(getattr(blocked_outbox, "blocked_detail_json", None))
    if detail.get("last_probe_result") == "escalated" and detail.get("diagnostic_key"):
        return
    from src.utils.timezone import timezone as timezone_utils

    diagnostic_key = _resource_wait_diagnostic_key(outbox_id, "DEVICE_STATUS_PRECHECK_WAIT")
    escalated_at = timezone_utils.now_utc().isoformat()
    elapsed_seconds = _resource_wait_elapsed_seconds(blocked_outbox)
    detail.update(
        {
            "last_probe_result": "escalated",
            "escalated_at": escalated_at,
            "diagnostic_key": diagnostic_key,
            "waited_seconds": elapsed_seconds,
            "ttl_seconds": DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS,
            "max_check_count": DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT,
        }
    )
    updater = getattr(outbox_repo, "update_resource_wait_detail", None)
    if not callable(updater):
        return
    updated_result = updater(
        db,
        outbox_id,
        expected_reason="DEVICE_STATUS_PRECHECK_WAIT",
        last_error=message,
        detail=detail,
    )
    if not isawaitable(updated_result):
        return
    updated = await updated_result
    if updated is not None:
        blocked_outbox = updated
    await _record_diagnostic(
        db,
        inbox=None,
        outbox=blocked_outbox or outbox,
        error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
        message=message,
        request_id=diagnostic_key,
        trace_id=diagnostic_key,
        extra={
            **_outbox_trace_extra(blocked_outbox or outbox),
            "reason": "DEVICE_STATUS_PRECHECK_WAIT_TIMEOUT",
            "diagnostic_key": diagnostic_key,
            "waited_seconds": elapsed_seconds,
            "blocked_check_count": getattr(blocked_outbox, "blocked_check_count", None),
        },
    )


async def _block_outbox_for_device_resource_wait(
    db: Any,
    *,
    outbox_repo: Any,
    outbox: Any,
    outbox_id: int,
    governance_error: _DeviceCommandGovernanceError,
    dispatch_attempt: Any | None = None,
    attempt_service: Any | None = None,
) -> bool:
    """目标设备接纳条件暂不满足时，暂停 outbox 并等待 ECS probe 重新放行。"""
    if governance_error.code not in _DEVICE_RESOURCE_WAIT_CODES:
        raise governance_error
    detail = _resource_wait_detail(governance_error)
    if governance_error.code == "DEVICE_STATUS_PRECHECK_WAIT":
        existing_detail = payload_dict(getattr(outbox, "blocked_detail_json", None))
        if existing_detail.get("last_probe_result") == "escalated" and existing_detail.get("diagnostic_key"):
            detail.update(
                {
                    "last_probe_result": "escalated",
                    "escalated_at": existing_detail.get("escalated_at"),
                    "diagnostic_key": existing_detail.get("diagnostic_key"),
                }
            )
    if dispatch_attempt is not None and attempt_service is not None:
        _ = await attempt_service.finalize_attempt_record(
            db,
            attempt=dispatch_attempt,
            success=False,
            error_message=governance_error.message,
            response={"result": "blocked_resource", "reason": governance_error.code, "detail": detail},
            auto_commit=False,
        )
    blocked_outbox = await outbox_repo.mark_as_blocked_by_device_busy(
        db,
        outbox_id,
        blocked_device_id=governance_error.device_id,
        blocked_workline_id=getattr(outbox, "workline_id", None),
        reason=governance_error.code,
        last_error=governance_error.message,
        detail=detail,
    )
    if blocked_outbox is None:
        await db.commit()
        logger.warning(
            f"Outbox {outbox_id} 因设备资源等待暂停时被 fencing 拒绝，保留当前状态 ({_outbox_trace_log_suffix(outbox)})"
        )
        return False
    await _escalate_status_precheck_wait_if_needed(
        db,
        outbox_repo=outbox_repo,
        outbox=outbox,
        outbox_id=outbox_id,
        blocked_outbox=blocked_outbox,
        message=governance_error.message,
    )
    await db.commit()
    logger.info(
        f"Outbox {outbox_id} 因设备 {governance_error.device_code or governance_error.device_id} "
        f"资源等待暂停派发: reason={governance_error.code}"
    )
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


def _is_dispatched_command(command: Any | None) -> bool:
    return enum_value(getattr(command, "status", None)) in {"SENT", "ACK_RECEIVED"}


async def _repair_orphaned_device_busy_dispatches(db: Any, *, outbox_repo: Any, limit: int) -> int:
    """恢复 attempt 已结束但 outbox 仍卡在 DISPATCHING 的设备忙派发。"""
    from src.app.device.repositories.device_repository import device_repository
    from src.app.runtime.orchestration.repositories.dispatch_attempt_repository import (
        workline_dispatch_attempt_repository,
    )

    getter = getattr(outbox_repo, "get_dispatching_device_messages", None)
    if not callable(getter):
        return 0
    repaired = 0
    dispatching_messages = getter(db, limit=limit, operation_domains=("WORKLINE", "RACK"))
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
        await db.commit()
    if repaired:
        logger.info(f"已恢复 {repaired} 条设备忙残留派发 outbox")
    return repaired


async def _repair_self_blocked_device_busy_dispatches(db: Any, *, outbox_repo: Any, limit: int) -> int:
    """禁用本地自阻塞放行；blocked 设备命令只能通过 ECS admission probe 恢复。"""
    _ = (db, outbox_repo, limit)
    return 0


async def _resolve_device_id_for_target_code(db: Any, target_code: str) -> Any | None:
    from src.app.device.repositories.device_repository import device_repository

    device = await device_repository.get_by_device_code(db, target_code)
    return resolve_entity_id(device)


async def _remember_blocked_head_device_scope(
    db: Any,
    *,
    outbox: Any,
    processed_device_ids: set[Any],
    processed_target_codes: set[str],
) -> tuple[Any | None, Any | None, str]:
    blocked_device_id = getattr(outbox, "blocked_device_id", None)
    device_id = getattr(outbox, "device_id", None)
    target_code = string_value(getattr(outbox, "target_code", None))
    if blocked_device_id is not None:
        processed_device_ids.add(blocked_device_id)
    if device_id is not None:
        processed_device_ids.add(device_id)
    if blocked_device_id is None and device_id is None and target_code:
        resolved_device_id = await _resolve_device_id_for_target_code(db, target_code)
        if resolved_device_id is not None:
            processed_device_ids.add(resolved_device_id)
    if target_code:
        processed_target_codes.add(target_code)
    return blocked_device_id, device_id, target_code


class OutboxDispatchService:
    MAX_RETRIES = 3

    def __init__(self, *, external_http_recovery_context_factory: Any | None = None) -> None:
        self.external_http_recovery_context_factory = external_http_recovery_context_factory

    def _resolve_external_http_recovery_context_factory(self) -> Any:
        if self.external_http_recovery_context_factory is None:
            from src.database.db import get_db_context

            self.external_http_recovery_context_factory = get_db_context
        return self.external_http_recovery_context_factory

    async def _finalize_external_http_result(
        self,
        db: Any,
        *,
        outbox_repo: Any,
        outbox_id: int,
        dispatch_attempt: Any,
        attempt_service: Any,
        result: ExternalHttpTransportResult,
    ) -> Any | None:
        """按唯一 typed transport result 同步终结 outbox 与 attempt。"""
        from src.app.sys.external_http_transport import ExternalHttpTransportOutcome

        error = result.error_message or result.error_code or result.outcome.value
        if result.outcome is ExternalHttpTransportOutcome.ACCEPTED:
            updated = await outbox_repo.mark_as_sent(db, outbox_id)
        elif result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS:
            updated = await outbox_repo.mark_as_unknown(db, outbox_id, error)
        elif result.safe_to_retry:
            updated = await outbox_repo.mark_as_failed(db, outbox_id, error, self.MAX_RETRIES)
        else:
            updated = await outbox_repo.mark_as_terminal_failure(db, outbox_id, error)

        outbox_finalization = "fenced" if updated is None else enum_value(getattr(updated, "status", None)).lower()
        _ = await attempt_service.finalize_external_http_attempt_record(
            db,
            attempt=dispatch_attempt,
            result=result,
            outbox_finalization=outbox_finalization,
            auto_commit=False,
        )
        return updated

    async def _prepare_claimed_blocked_head_dispatch(
        self,
        db: Any,
        *,
        outbox_repo: Any,
        claimed: Any,
        outbox_id: int,
        attempt_service: Any,
        safety_service: Any,
        result: DispatchResult,
    ) -> tuple[Any, bool]:
        """blocked 队首 claim 后创建 attempt，并在 POST 前执行 WorkLine final guard。"""

        dispatch_attempt = await attempt_service.create_attempt(
            db,
            outbox=claimed,
            auto_commit=False,
        )
        await db.commit()
        outbox_workline_id = getattr(claimed, "workline_id", None)
        if outbox_workline_id is None:
            return dispatch_attempt, False
        try:
            await safety_service.assert_accepting_work(db, workline_id=outbox_workline_id)
        except WorkLineSafetyBlocked as safety_error:
            block_state = await _block_outbox_for_workline_safety(
                db,
                outbox_repo=outbox_repo,
                outbox=claimed,
                outbox_id=outbox_id,
                safety_error=safety_error,
                trace=TraceContext.from_runtime(outbox=claimed),
                dispatch_attempt=dispatch_attempt,
                attempt_service=attempt_service,
                final_guard=True,
            )
            _count_workline_safety_block_result(result, block_state)
            return dispatch_attempt, True
        await db.commit()
        return dispatch_attempt, False

    async def _probe_blocked_resource_head_ready(self, db: Any, outbox: Any) -> bool:
        """claim blocked 队首前只做 ECS admission probe，不产生设备命令 POST 副作用。"""
        import httpx

        from src.app.device.repositories.device_repository import device_repository
        from src.app.runtime.orchestration.services.device_command_gateway import (
            _ensure_realtime_device_status_ready,
            _get_device_for_command_dispatch,
        )

        device = await _get_device_for_command_dispatch(db, device_repository, outbox.target_code)
        if device is None or not getattr(device, "host", None) or not getattr(device, "port", None):
            device_id = resolve_entity_id(device) or getattr(outbox, "blocked_device_id", None)
            device_code = string_value(getattr(device, "device_code", None), default=outbox.target_code)
            message = f"设备 {device_code} 通信配置不完整，等待下次预检"
            logger.warning(f"blocked outbox ECS probe 缺少设备通信配置: target_code={outbox.target_code}")
            raise _DeviceCommandGovernanceError(
                domain="ORCHESTRATION",
                code="DEVICE_STATUS_PRECHECK_WAIT",
                message=message,
                device_id=device_id,
                device_code=device_code,
                detail={
                    "device_code": device_code,
                    "error_kind": "missing_device_config",
                    "host_configured": bool(getattr(device, "host", None)),
                    "port_configured": bool(getattr(device, "port", None)),
                    "last_probe_result": "missing_device_config",
                },
            )

        payload = payload_dict(getattr(outbox, "payload_json", None))
        command_code = string_value(payload.get("command_code"))
        device_code = string_value(getattr(device, "device_code", None), default=outbox.target_code)
        async with httpx.AsyncClient() as client:
            _ = await _ensure_realtime_device_status_ready(
                client=client,
                device=device,
                device_code=device_code,
                command_code=command_code or None,
            )
        return True

    async def _dispatch_blocked_resource_heads(
        self,
        db: Any,
        *,
        outbox_repo: Any,
        limit: int,
        result: DispatchResult,
    ) -> tuple[set[Any], set[str]]:
        """优先探测资源等待队首，ECS ready 后领取并复用设备 POST。"""
        from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
            workline_dispatch_attempt_service,
        )
        from src.app.workline.services.safety_service import workline_safety_service

        processed_device_ids: set[Any] = set()
        processed_target_codes: set[str] = set()
        getter = getattr(outbox_repo, "get_probeable_blocked_device_heads", None)
        if not callable(getter):
            return processed_device_ids, processed_target_codes

        blocked_heads_result = getter(
            db,
            limit=limit,
            min_probe_interval_seconds=RESOURCE_WAIT_PROBE_MIN_INTERVAL_SECONDS,
            operation_domains=("WORKLINE", "RACK"),
        )
        if not isawaitable(blocked_heads_result):
            return processed_device_ids, processed_target_codes
        blocked_heads = await blocked_heads_result
        for outbox in blocked_heads:
            if result["dispatched"] >= limit:
                break
            outbox_id = resolve_entity_id(outbox)
            if outbox_id is None:
                result["skipped"] += 1
                continue
            blocked_device_id, device_id, target_code = await _remember_blocked_head_device_scope(
                db,
                outbox=outbox,
                processed_device_ids=processed_device_ids,
                processed_target_codes=processed_target_codes,
            )
            blocked_reason = string_value(getattr(outbox, "blocked_reason", None), default="DEVICE_BUSY")
            claimed: Any | None = None
            dispatch_attempt: Any | None = None
            try:
                ready = await self._probe_blocked_resource_head_ready(db, outbox)
                if not ready:
                    result["skipped"] += 1
                    result["dispatched"] += 1
                    continue
                claimed = await outbox_repo.claim_blocked_resource_wait_for_dispatch(
                    db,
                    outbox_id,
                    blocked_reason,
                    min_probe_interval_seconds=RESOURCE_WAIT_PROBE_MIN_INTERVAL_SECONDS,
                    operation_domains=("WORKLINE", "RACK"),
                )
                if claimed is None:
                    await db.commit()
                    result["skipped"] += 1
                    result["dispatched"] += 1
                    continue
                await db.commit()
                dispatch_attempt, blocked_by_safety = await self._prepare_claimed_blocked_head_dispatch(
                    db,
                    outbox_repo=outbox_repo,
                    claimed=claimed,
                    outbox_id=outbox_id,
                    attempt_service=workline_dispatch_attempt_service,
                    safety_service=workline_safety_service,
                    result=result,
                )
                if blocked_by_safety:
                    continue
                success = await self._dispatch_single(db, claimed)
                if success:
                    sent_outbox = await outbox_repo.mark_as_sent(db, outbox_id)
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
                    await db.commit()
                    if sent_outbox is None:
                        result["skipped"] += 1
                    else:
                        result["success"] += 1
                    result["dispatched"] += 1
                    continue
                failed_outbox = await outbox_repo.mark_as_failed(
                    db,
                    outbox_id,
                    string_value(getattr(claimed, "_dispatch_failure_reason", None), default="Dispatch failed"),
                    self.MAX_RETRIES,
                )
                if dispatch_attempt is not None:
                    _ = await workline_dispatch_attempt_service.finalize_attempt_record(
                        db,
                        attempt=dispatch_attempt,
                        success=False,
                        error_message=string_value(
                            getattr(claimed, "_dispatch_failure_reason", None),
                            default="Dispatch failed",
                        ),
                        auto_commit=False,
                    )
                await db.commit()
                result["failed" if failed_outbox is not None else "skipped"] += 1
                result["dispatched"] += 1
            except _DeviceCommandGovernanceError as e:
                if e.code in _DEVICE_RESOURCE_WAIT_CODES:
                    _ = await _block_outbox_for_device_resource_wait(
                        db,
                        outbox_repo=outbox_repo,
                        outbox=claimed or outbox,
                        outbox_id=outbox_id,
                        governance_error=e,
                        dispatch_attempt=dispatch_attempt,
                        attempt_service=workline_dispatch_attempt_service,
                    )
                    result["skipped"] += 1
                    result["dispatched"] += 1
                    continue
                logger.warning(
                    f"blocked outbox {outbox_id} ECS probe 治理拒绝: {e.message} ({_outbox_trace_log_suffix(outbox)})"
                )
                if dispatch_attempt is not None:
                    _ = await workline_dispatch_attempt_service.finalize_attempt_record(
                        db,
                        attempt=dispatch_attempt,
                        success=False,
                        error_message=e.message,
                        response={"result": "failed", "reason": e.code},
                        auto_commit=False,
                    )
                _ = await outbox_repo.mark_as_failed(db, outbox_id, e.message, self.MAX_RETRIES)
                await db.commit()
                result["failed"] += 1
                result["dispatched"] += 1
            except Exception as e:
                logger.warning(f"blocked outbox {outbox_id} ECS probe 异常: {e}")
                if claimed is None:
                    _ = await _block_outbox_for_device_resource_wait(
                        db,
                        outbox_repo=outbox_repo,
                        outbox=outbox,
                        outbox_id=outbox_id,
                        governance_error=_DeviceCommandGovernanceError(
                            domain="ORCHESTRATION",
                            code="DEVICE_STATUS_PRECHECK_WAIT",
                            message=str(e),
                            device_id=blocked_device_id or device_id,
                            device_code=target_code or None,
                            detail={
                                "device_code": target_code,
                                "error_kind": "probe_exception",
                                "last_probe_result": "exception",
                                "error_message": str(e),
                            },
                        ),
                    )
                    result["skipped"] += 1
                    result["dispatched"] += 1
                    continue
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
                        logger.warning(f"blocked outbox {outbox_id} 派发尝试账本补记失败: {attempt_error}")
                failed_outbox = await outbox_repo.mark_as_failed(db, outbox_id, str(e), self.MAX_RETRIES)
                await db.commit()
                result["failed" if failed_outbox is not None else "skipped"] += 1
                result["dispatched"] += 1
        return processed_device_ids, processed_target_codes

    async def dispatch(self, db: Any, limit: int = 50) -> DispatchResult:  # noqa: PLR0912
        """派发 Outbox 消息
        Args:
            db: 数据库会话
            limit: 批处理数量
        Returns:
            派发结果统计
        """
        from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
            workline_dispatch_attempt_service,
        )
        from src.app.sys.repositories import SystemOutboxRepository
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
        processed_blocked_device_ids, processed_blocked_target_codes = await self._dispatch_blocked_resource_heads(
            db,
            outbox_repo=outbox_repo,
            limit=limit,
            result=result,
        )
        remaining_limit = max(limit - result["dispatched"], 0)
        if remaining_limit <= 0:
            await db.commit()
            from src.app.sys.services.event_stream_service import publish_deferred_sse_events

            await publish_deferred_sse_events(db)
            return result
        messages = await outbox_repo.get_pending_messages(
            db, limit=remaining_limit, operation_domains=("WORKLINE", "RACK")
        )
        device_repo_for_skip: Any | None = None
        for outbox in messages:
            device_id = getattr(outbox, "device_id", None)
            target_code = string_value(getattr(outbox, "target_code", None))
            if device_id is None and target_code and processed_blocked_device_ids:
                if device_repo_for_skip is None:
                    from src.app.device.repositories.device_repository import device_repository

                    device_repo_for_skip = device_repository
                device = await device_repo_for_skip.get_by_device_code(db, target_code)
                device_id = resolve_entity_id(device)
            if (
                device_id is not None and device_id in processed_blocked_device_ids
            ) or target_code in processed_blocked_target_codes:
                result["skipped"] += 1
                continue
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
                dispatch_result = await self._dispatch_single(db, outbox)
                from src.app.sys.external_http_transport import (
                    ExternalHttpTransportOutcome,
                    ExternalHttpTransportResult,
                )

                if isinstance(dispatch_result, ExternalHttpTransportResult):
                    try:
                        if dispatch_attempt is None:
                            raise RuntimeError("EXTERNAL_HTTP 派发缺少 attempt 证据")
                        updated = await self._finalize_external_http_result(
                            db,
                            outbox_repo=outbox_repo,
                            outbox_id=outbox_pk,
                            dispatch_attempt=dispatch_attempt,
                            attempt_service=workline_dispatch_attempt_service,
                            result=dispatch_result,
                        )
                        await db.commit()
                    except Exception as evidence_error:
                        _ = await recover_external_http_evidence_failure_unknown(
                            db,
                            outbox_repository=outbox_repo,
                            outbox_id=outbox_pk,
                            result=dispatch_result,
                            cause=evidence_error,
                            recovery_context_factory=self._resolve_external_http_recovery_context_factory(),
                        )
                        logger.exception(f"Outbox {outbox_pk} 证据落库失败，已隔离收口为 UNKNOWN")
                        result["failed"] += 1
                        result["dispatched"] += 1
                        continue
                    if updated is None:
                        result["skipped"] += 1
                        logger.warning(
                            f"Outbox {outbox_pk} EXTERNAL_HTTP 终态更新被 fencing 拒绝，保留 attempt 证据 "
                            f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                        )
                    elif dispatch_result.outcome is ExternalHttpTransportOutcome.ACCEPTED:
                        result["success"] += 1
                        logger.info(
                            f"Outbox {outbox_pk} EXTERNAL_HTTP transport 已送达 "
                            f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                        )
                    else:
                        result["failed"] += 1
                        logger.warning(
                            f"Outbox {outbox_pk} EXTERNAL_HTTP transport={dispatch_result.outcome.value}，禁止非安全重放 "
                            f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                        )
                    result["dispatched"] += 1
                    continue
                if dispatch_result:
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
            except ExternalHttpEvidenceRecoveryError:
                # UNKNOWN 隔离恢复失败时必须向上抛出，禁止落入通用 mark_as_failed 自动重试路径。
                raise
            except _DeviceCommandGovernanceError as e:
                outbox_pk = resolve_entity_id(outbox)
                if e.code in _DEVICE_RESOURCE_WAIT_CODES:
                    logger.info(
                        f"Outbox {outbox_pk_text} 等待目标设备资源: {e.message} "
                        f"({_outbox_trace_log_suffix(outbox, trace=trace)})"
                    )
                    try:
                        if outbox_pk is not None:
                            _ = await _block_outbox_for_device_resource_wait(
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

    async def _dispatch_single(self, db: Any, outbox: Any) -> bool | ExternalHttpTransportResult:
        """派发单个 Outbox 消息
        Args:
            db: 数据库会话
            outbox: Outbox 消息
        Returns:
            是否成功
        """
        from src.app.sys.models import SystemOutboxDispatchType

        _emit_runtime_intent_dispatch_observability(outbox)
        if await self._should_dispatch_to_sandbox(db, outbox):
            return await self._dispatch_sandbox(db, outbox)
        if outbox.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND:
            from src.app.runtime.orchestration.services.device_command_gateway import device_command_gateway

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

    async def _dispatch_sandbox(self, db: Any, outbox: Any) -> bool | ExternalHttpTransportResult:
        """派发到沙箱工作台。
        沙箱不改写 payload；Outbox 标记 SENT 后，由调试人员按原 callback/result 协议手工回传。
        对设备命令，SENT 已代表硬件侧待完成任务，必须同步占用设备运行态，避免沙箱假并发。
        """
        from src.app.sys.models import SystemOutboxDispatchType

        if outbox.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND:
            from src.app.runtime.orchestration.services.device_command_gateway import device_command_gateway

            reserved = await device_command_gateway.reserve_sandbox_command(db, outbox)
            if not reserved:
                return False
            payload = payload_dict(getattr(outbox, "payload_json", None))
            logger.info(f"沙箱设备指令参数: {_build_device_command_log_envelope(outbox, payload)}")
        elif outbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP:
            from src.app.sys.external_http_transport import ExternalHttpTransportResult

            logger.info(
                "Outbox 沙箱派发完成，等待调试人员手工回调 "
                f"({_outbox_trace_log_suffix(outbox)}, session_id={getattr(outbox, 'session_id', None)})"
            )
            return ExternalHttpTransportResult.sandbox_accepted()
        logger.info(
            "Outbox 沙箱派发完成，等待调试人员手工回调 "
            f"({_outbox_trace_log_suffix(outbox)}, session_id={getattr(outbox, 'session_id', None)})"
        )
        return True

    async def _dispatch_external_http(self, outbox: Any) -> ExternalHttpTransportResult:
        """派发外部 HTTP 决策（如过站回传）。"""
        from src.app.sys.services.outbox_delivery import dispatch_external_http
        from src.app.sys.services.outbox_engine import _send_external_http, endpoint_registry

        return await dispatch_external_http(outbox, endpoint_registry, _send_external_http)

    async def _dispatch_internal_signal(self, outbox: Any) -> bool:
        """派发内部微服务解耦信号（如释放货位）。"""
        from src.app.sys.services.outbox_delivery import dispatch_internal_signal

        return await dispatch_internal_signal(outbox)


outbox_dispatch_service = OutboxDispatchService()
