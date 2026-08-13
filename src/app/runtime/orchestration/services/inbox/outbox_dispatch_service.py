from __future__ import annotations

from socket import gethostname
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import uuid4

if TYPE_CHECKING:
    from src.app.sys.external_http_transport import ExternalHttpTransportResult
    from src.app.workline.services.safety_service import WorkLineSafetyBlocked

from src.app.runtime.orchestration.diagnostics import ErrorCode
from src.app.sys.dispatch_concurrency import FairDispatchScheduler, dispatch_policy_registry
from src.app.sys.external_http_dispatch_faults import (
    ExternalHttpDispatchFaultHook,
    ExternalHttpDispatchFaultPoint,
    emit_external_http_dispatch_fault,
)
from src.app.sys.external_http_evidence import (
    ExternalHttpEvidenceRecoveryError,
    is_late_external_http_result_target,
    recover_external_http_evidence_failure_unknown,
)
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.domain.run_mode import is_simulation_run_mode
from src.app.workline.outbox_dispatch_support import (
    _outbox_trace_extra,
    _outbox_trace_log_suffix,
    _resolve_outbox_run_mode,
)
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


class DispatchResult(TypedDict):
    dispatched: int
    success: int
    failed: int
    skipped: int


def _count_workline_safety_block_result(result: DispatchResult, block_state: str) -> None:
    if block_state in {"blocked_resource", "fenced"}:
        result["skipped"] += 1
    else:
        result["failed"] += 1
    result["dispatched"] += 1


async def _mark_outbox_blocked_by_workline_state(
    db: Any,
    *,
    outbox_repo: Any,
    outbox: Any,
    outbox_id: int,
    safety_error: Exception,
    lease_owner_token: str | None = None,
) -> str:
    """按现役 WorkLine 状态封存共享 Outbox；不包含任何设备命令语义。"""

    reason = str(safety_error)
    if "WORKLINE_RECONCILING" in reason:
        from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
            workline_runtime_reconciliation_service,
        )

        updated = await workline_runtime_reconciliation_service.park_outbox_for_reconciliation(
            db,
            outbox=outbox,
            reason="CALLBACK_DEADLINE_EXPIRED",
            lease_owner_token=lease_owner_token,
        )
        return "blocked_resource" if updated is not None else "fenced"

    if "WORKLINE_STOPPED" in reason:
        updated = await outbox_repo.mark_as_blocked_by_workline_stopped(
            db,
            outbox_id,
            lease_owner_token=lease_owner_token,
        )
        return "blocked_resource" if updated is not None else "fenced"

    updated = await outbox_repo.mark_as_blocked_by_workline_estop(
        db,
        outbox_id,
        lease_owner_token=lease_owner_token,
    )
    return "failed" if updated is not None else "fenced"


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
    lease_owner_token: str | None = None,
) -> str:
    await _record_diagnostic(
        db,
        inbox=None,
        outbox=outbox,
        error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
        message=str(safety_error),
        extra=_outbox_trace_extra(outbox, trace=trace),
    )
    block_state = await _mark_outbox_blocked_by_workline_state(
        db,
        outbox_repo=outbox_repo,
        outbox=outbox,
        outbox_id=outbox_id,
        safety_error=safety_error,
        lease_owner_token=lease_owner_token,
    )
    if block_state != "fenced" and dispatch_attempt is not None and attempt_service is not None:
        _ = await attempt_service.finalize_attempt_record(
            db,
            attempt=dispatch_attempt,
            lease_owner_token=lease_owner_token,
            success=False,
            error_message=str(safety_error),
            auto_commit=False,
        )
    await db.commit()
    guard_label = "最终安全状态" if final_guard else "安全状态"
    logger.warning(
        f"Outbox {outbox_id} 因 WorkLine {guard_label}阻断派发 ({_outbox_trace_log_suffix(outbox, trace=trace)})"
    )
    return block_state


class OutboxDispatchService:
    MAX_RETRIES = 3

    def __init__(
        self,
        *,
        external_http_recovery_context_factory: Any | None = None,
        effect_transport_bridge: Any | None = None,
        credential_provider: Any | None = None,
        external_http_sender: Any | None = None,
        outbox_repository: Any | None = None,
        dispatch_scheduler: Any | None = None,
        dispatch_attempt_service: Any | None = None,
        external_http_fault_hook: ExternalHttpDispatchFaultHook | None = None,
    ) -> None:
        if outbox_repository is None:
            from src.app.sys.repositories import system_outbox_repository

            outbox_repository = system_outbox_repository
        self.external_http_recovery_context_factory = external_http_recovery_context_factory
        self.effect_transport_bridge = effect_transport_bridge
        if credential_provider is None:
            from src.app.sys.external_http_credentials import external_http_credential_provider

            credential_provider = external_http_credential_provider
        if external_http_sender is None:
            from src.app.sys.services.outbox_engine import _send_external_http

            external_http_sender = _send_external_http
        self.credential_provider = credential_provider
        self.external_http_sender = external_http_sender
        self.outbox_repository = outbox_repository
        self.dispatch_attempt_service = dispatch_attempt_service
        # 仅通过构造器显式注入；生产 singleton 默认禁用，不提供环境变量或全局开关。
        self.external_http_fault_hook = external_http_fault_hook
        self.dispatch_scheduler = dispatch_scheduler or FairDispatchScheduler(
            repository=outbox_repository,
            policy_registry=dispatch_policy_registry,
            worker_identity=f"workline-outbox:{gethostname()}:{uuid4().hex}",
        )

    def _resolve_external_http_recovery_context_factory(self) -> Any:
        if self.external_http_recovery_context_factory is None:
            from src.database.db import get_db_context

            self.external_http_recovery_context_factory = get_db_context
        return self.external_http_recovery_context_factory

    def _resolve_effect_transport_bridge(self) -> Any:
        if self.effect_transport_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_transport_bridge

            self.effect_transport_bridge = effect_transport_bridge
        return self.effect_transport_bridge

    async def _finalize_external_http_result(
        self,
        db: Any,
        *,
        outbox_repo: Any,
        outbox: Any | None = None,
        outbox_id: int,
        dispatch_attempt: Any,
        attempt_service: Any,
        result: ExternalHttpTransportResult,
        lease_owner_token: str,
        retry_budget: int,
    ) -> Any | None:
        """按唯一 typed transport result 同步终结 outbox 与 attempt。"""
        from src.app.sys.external_http_transport import (
            ExternalHttpProtocolResult,
            ExternalHttpTransportOutcome,
        )

        error = result.error_message or result.error_code or result.outcome.value
        await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.BEFORE_OUTBOX_EVIDENCE, outbox)
        if result.outcome is ExternalHttpTransportOutcome.ACCEPTED:
            if result.protocol_result is ExternalHttpProtocolResult.REJECTED:
                updated = await outbox_repo.mark_as_protocol_rejected(
                    db,
                    outbox_id,
                    error,
                    lease_owner_token=lease_owner_token,
                )
            else:
                updated = await outbox_repo.mark_as_sent(db, outbox_id, lease_owner_token=lease_owner_token)
        elif result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS:
            updated = await outbox_repo.mark_as_unknown(
                db,
                outbox_id,
                error,
                lease_owner_token=lease_owner_token,
            )
        elif result.safe_to_retry:
            updated = await outbox_repo.mark_as_failed(
                db,
                outbox_id,
                error,
                retry_budget,
                lease_owner_token=lease_owner_token,
            )
        else:
            updated = await outbox_repo.mark_as_terminal_failure(
                db,
                outbox_id,
                error,
                lease_owner_token=lease_owner_token,
            )
        await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.AFTER_OUTBOX_EVIDENCE, outbox)

        if updated is None:
            current = await outbox_repo.get_by_id_for_update(db, outbox_id)
            if enum_value(getattr(current, "status", None)) != "SENT":
                if is_late_external_http_result_target(current) and outbox is not None:
                    # UNKNOWN 账本保持不可改写；
                    # 晚到 typed result 仍须补入 open reconciliation case。
                    await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.BEFORE_REDUCER_EVIDENCE, outbox)
                    from src.utils.timezone import timezone

                    await self._resolve_effect_transport_bridge().record_result(
                        db,
                        dispatch_key=str(outbox.dispatch_key),
                        attempt_no=int(getattr(dispatch_attempt, "attempt_no", None) or 1),
                        result=result,
                        retry_exhausted=False,
                        occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
                        operation_identity=getattr(outbox, "operation_identity", None),
                        payload_json=payload_dict(getattr(outbox, "payload_json", None)),
                        idempotency_key=getattr(outbox, "idempotency_key", None),
                        payload_hash=getattr(outbox, "payload_hash", None),
                    )
                    await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.AFTER_REDUCER_EVIDENCE, outbox)
                return None
            # callback 可在 sender 返回前先把 outbox 收口为 SENT；当前 attempt 仍须同步终结。
            updated = current
        outbox_finalization = enum_value(getattr(updated, "status", None)).lower()
        await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.BEFORE_ATTEMPT_EVIDENCE, outbox)
        _ = await attempt_service.finalize_external_http_attempt_record(
            db,
            attempt=dispatch_attempt,
            lease_owner_token=lease_owner_token,
            result=result,
            outbox_finalization=outbox_finalization,
            auto_commit=False,
        )
        await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.AFTER_ATTEMPT_EVIDENCE, outbox)
        if updated is not None and outbox is not None:
            await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.BEFORE_REDUCER_EVIDENCE, outbox)
            from src.utils.timezone import timezone

            await self._resolve_effect_transport_bridge().record_result(
                db,
                dispatch_key=str(outbox.dispatch_key),
                attempt_no=int(getattr(dispatch_attempt, "attempt_no", None) or 1),
                result=result,
                retry_exhausted=enum_value(getattr(updated, "status", None)) == "FAILED",
                occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
                operation_identity=getattr(outbox, "operation_identity", None),
                payload_json=payload_dict(getattr(outbox, "payload_json", None)),
                idempotency_key=getattr(outbox, "idempotency_key", None),
                payload_hash=getattr(outbox, "payload_hash", None),
            )
            await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.AFTER_REDUCER_EVIDENCE, outbox)
        return updated

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
        from src.app.workline.services.safety_service import WorkLineSafetyBlocked, workline_safety_service

        result: DispatchResult = {
            "dispatched": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }
        outbox_repo = self.outbox_repository
        attempt_service = self.dispatch_attempt_service or workline_dispatch_attempt_service
        remaining_limit = limit
        if remaining_limit <= 0:
            await db.commit()
            from src.app.sys.services.event_stream_service import publish_deferred_sse_events

            await publish_deferred_sse_events(db)
            return result
        await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.BEFORE_CLAIM)
        claim_batch = await self.dispatch_scheduler.claim(
            db,
            limit=remaining_limit,
            operation_domains=("WORKLINE", "RACK"),
        )
        logger.info(
            "Workline SystemOutbox claim metrics: "
            f"backlog={claim_batch.metrics.backlog_count}, active={claim_batch.metrics.active_lease_count}, "
            f"unknown={claim_batch.metrics.unknown_count}, oldest_age={claim_batch.metrics.oldest_queue_age_seconds}, "
            f"rate_limited={len(claim_batch.metrics.rate_limited_buckets)}, "
            f"paused={len(claim_batch.metrics.paused_buckets)}, "
            f"contended={len(claim_batch.metrics.lease_contended_buckets)}, "
            f"lease_loss={claim_batch.metrics.lease_loss_count}"
        )
        try:
            from src.app.runtime.orchestration.operation_observability import emit_dispatch_health_observation

            _ = emit_dispatch_health_observation(claim_batch.metrics)
        except Exception as exc:  # pragma: no cover - 观测失败不改变 claim/事务边界
            logger.warning(f"Workline SystemOutbox claim observability emission failed: {type(exc).__name__}")
        attempts_by_outbox_id: dict[int, Any] = {}
        for claim in claim_batch.claims:
            outbox_id = resolve_required_pk(claim.outbox, "outbox", "id", "outbox_id")
            attempts_by_outbox_id[outbox_id] = await attempt_service.create_attempt(
                db,
                outbox=claim.outbox,
                auto_commit=False,
            )
        await db.commit()

        for claim in claim_batch.claims:
            outbox = claim.outbox
            lease_owner_token = claim.lease_owner_token
            retry_budget = claim.policy.retry_budget
            outbox_id = resolve_required_pk(outbox, "outbox", "id", "outbox_id")
            outbox_pk_text = str(getattr(outbox, "id", "unknown"))
            trace = TraceContext.from_runtime(outbox=outbox)
            dispatch_attempt: Any | None = attempts_by_outbox_id.get(outbox_id)
            if enum_value(getattr(outbox, "dispatch_type", None)) == "EXTERNAL_HTTP":
                await self._emit_external_http_fault(
                    ExternalHttpDispatchFaultPoint.AFTER_CLAIM_COMMIT,
                    outbox,
                )
            try:
                outbox_pk = outbox_id
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
                            dispatch_attempt=dispatch_attempt,
                            attempt_service=attempt_service,
                            final_guard=True,
                            lease_owner_token=lease_owner_token,
                        )
                        _count_workline_safety_block_result(result, block_state)
                        continue
                current_outbox = await outbox_repo.begin_physical_dispatch(
                    db,
                    outbox_pk,
                    lease_owner_token=lease_owner_token,
                    lease_seconds=claim.policy.lease_seconds,
                )
                if current_outbox is None:
                    await db.commit()
                    result["skipped"] += 1
                    result["dispatched"] += 1
                    continue
                outbox = current_outbox
                trace = TraceContext.from_runtime(outbox=outbox)
                # 释放发送边界事务的行锁，typed callback 可在 sender response 前完成。
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
                            outbox=outbox,
                            outbox_id=outbox_pk,
                            dispatch_attempt=dispatch_attempt,
                            attempt_service=attempt_service,
                            result=dispatch_result,
                            lease_owner_token=lease_owner_token,
                            retry_budget=retry_budget,
                        )
                        await db.commit()
                    except Exception as evidence_error:
                        _ = await recover_external_http_evidence_failure_unknown(
                            db,
                            outbox_repository=outbox_repo,
                            outbox_id=outbox_pk,
                            lease_owner_token=lease_owner_token,
                            result=dispatch_result,
                            cause=evidence_error,
                            recovery_context_factory=self._resolve_external_http_recovery_context_factory(),
                            attempt_service=attempt_service,
                            effect_transport_bridge=self._resolve_effect_transport_bridge(),
                            dispatch_key=str(outbox.dispatch_key),
                            attempt_no=int(getattr(dispatch_attempt, "attempt_no", None) or 1),
                            operation_identity=getattr(outbox, "operation_identity", None),
                            payload_json=payload_dict(getattr(outbox, "payload_json", None)),
                            idempotency_key=getattr(outbox, "idempotency_key", None),
                            payload_hash=getattr(outbox, "payload_hash", None),
                        )
                        logger.exception(f"Outbox {outbox_pk} 证据落库失败，已隔离收口为 UNKNOWN")
                        result["failed"] += 1
                        result["dispatched"] += 1
                        continue
                    await self._emit_external_http_fault(
                        ExternalHttpDispatchFaultPoint.AFTER_EVIDENCE_COMMIT,
                        outbox,
                    )
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
                    sent_outbox = await outbox_repo.mark_as_sent(
                        db,
                        outbox_pk,
                        lease_owner_token=lease_owner_token,
                    )
                    if dispatch_attempt is not None and sent_outbox is not None:
                        _ = await attempt_service.finalize_attempt_record(
                            db,
                            attempt=dispatch_attempt,
                            lease_owner_token=lease_owner_token,
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
                    failed_outbox = await outbox_repo.mark_as_failed(
                        db,
                        outbox_pk,
                        failure_reason,
                        retry_budget,
                        lease_owner_token=lease_owner_token,
                    )
                    if dispatch_attempt is not None and failed_outbox is not None:
                        _ = await attempt_service.finalize_attempt_record(
                            db,
                            attempt=dispatch_attempt,
                            lease_owner_token=lease_owner_token,
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
                        error_code=_dispatch_failure_diagnostic_code(failed_outbox),
                        message=failure_reason,
                        extra=trace_extra,
                    )
                    await db.commit()
                    result["failed"] += 1
                    logger.warning(f"Outbox {outbox_pk} 派发失败 ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                result["dispatched"] += 1
            except ExternalHttpEvidenceRecoveryError:
                # UNKNOWN 隔离恢复失败时必须向上抛出，禁止落入通用 mark_as_failed 自动重试路径。
                raise
            except Exception as e:
                logger.exception(f"Outbox {outbox_pk_text} 派发异常 ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                try:
                    outbox_pk = resolve_entity_id(outbox)
                    if outbox_pk is not None:
                        failed_outbox = await outbox_repo.mark_as_failed(
                            db,
                            outbox_pk,
                            str(e),
                            retry_budget,
                            lease_owner_token=lease_owner_token,
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
                        if dispatch_attempt is not None:
                            try:
                                _ = await attempt_service.finalize_attempt_record(
                                    db,
                                    attempt=dispatch_attempt,
                                    lease_owner_token=lease_owner_token,
                                    success=False,
                                    error_message=str(e),
                                    auto_commit=False,
                                )
                            except Exception as attempt_error:
                                logger.warning(f"Outbox {outbox_pk_text} 派发尝试账本补记失败: {attempt_error}")
                        await _record_diagnostic(
                            db,
                            inbox=None,
                            outbox=outbox,
                            error_code=_dispatch_failure_diagnostic_code(failed_outbox),
                            message=str(e),
                            extra=_outbox_trace_extra(outbox, trace=trace),
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
        if outbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP:
            await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.BEFORE_SEND, outbox)
            result = await self._dispatch_external_http(outbox)
            await self._emit_external_http_fault(ExternalHttpDispatchFaultPoint.AFTER_SEND, outbox)
            return result
        if outbox.dispatch_type == SystemOutboxDispatchType.INTERNAL_SIGNAL:
            return await self._dispatch_internal_signal(outbox)
        logger.warning(f"未知的派发类型: {outbox.dispatch_type}")
        return False

    async def _emit_external_http_fault(
        self,
        point: ExternalHttpDispatchFaultPoint,
        outbox: Any | None = None,
    ) -> None:
        await emit_external_http_dispatch_fault(self.external_http_fault_hook, point, outbox)

    async def _should_dispatch_to_sandbox(self, db: Any, outbox: Any) -> bool:
        """判断 Outbox 是否应进入沙箱派发出口。"""
        from src.app.sys.models import SystemOutboxDispatchType

        if outbox.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP:
            return False
        run_mode = await _resolve_outbox_run_mode(db, outbox)
        return is_simulation_run_mode(run_mode)

    async def _dispatch_sandbox(self, db: Any, outbox: Any) -> bool | ExternalHttpTransportResult:
        """派发外部 HTTP 到沙箱工作台，等待调试人员手工回调。"""
        from src.app.sys.models import SystemOutboxDispatchType

        _ = db

        if outbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP:
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

        return await dispatch_external_http(outbox, self.credential_provider, self.external_http_sender)

    async def _dispatch_internal_signal(self, outbox: Any) -> bool:
        """派发内部微服务解耦信号（如释放货位）。"""
        from src.app.sys.services.outbox_delivery import dispatch_internal_signal

        return await dispatch_internal_signal(outbox)


outbox_dispatch_service = OutboxDispatchService()
