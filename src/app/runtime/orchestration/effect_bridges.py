"""Transport、callback 与 reconciliation 到唯一 EFFECT reducer 的 typed bridge。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    generated_effect_source_event_id,
)
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer, effect_reducer
from src.app.runtime.orchestration.wms_sync_obligation import WMS_SYNC_OBLIGATION_OPERATION_IDENTITIES
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.effect_runtime import (
    interpret_async_effect_ack_response,
    interpret_sync_effect_response,
    typed_wms_effect_ack_hash,
)
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import (
    EFFECT_OPERATION_IDENTITIES,
    WMS_OPERATION_BY_IDENTITY,
)

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
    from src.app.runtime.orchestration.wms_sync_obligation import WmsSyncObligationResolution


class EffectCallbackOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class EffectTransportAction(str, Enum):
    """Outbox finalizer 对 typed EFFECT transport result 的附加动作。"""

    DEFAULT = "DEFAULT"
    RETRY_SAME_REQUEST = "RETRY_SAME_REQUEST"


@dataclass(frozen=True, slots=True)
class EffectTransportResolution:
    """一次 transport result 的 reducer events 与 Outbox 动作。"""

    events: tuple[EffectReducerEvent, ...]
    action: EffectTransportAction = EffectTransportAction.DEFAULT


def _expected_wms_idempotency_code(
    result: ExternalHttpTransportResult,
    *,
    protocol_rejection: bool,
) -> str | None:
    """把 WMS submit 的幂等 HTTP 状态收敛为稳定协议码。"""

    if not protocol_rejection:
        return None
    if result.http_status_code == 409:
        return "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    if result.http_status_code == 422:
        return "IDEMPOTENCY_CONFLICT"
    return None


def _transport_evidence(
    result: ExternalHttpTransportResult,
    *,
    operation_identity: str | None,
) -> dict[str, Any]:
    evidence = result.evidence_json()
    if operation_identity is not None:
        evidence = {**evidence, "operation_identity": operation_identity}
    return evidence


def _reconciliation_event(
    *,
    dispatch_key: str,
    attempt_no: int,
    occurred_at_ms: int,
    reason_code: str,
    evidence_json: dict[str, Any],
) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=EffectReducerEventType.RECONCILIATION_OPENED,
        dispatch_key=dispatch_key,
        occurred_at_ms=occurred_at_ms,
        source_event_id=generated_effect_source_event_id(
            "transport-reconciliation",
            dispatch_key,
            attempt_no,
            reason_code,
            evidence_json,
        ),
        reason_code=reason_code,
        evidence_json=evidence_json,
    )


def _sync_transport_resolution(
    *,
    dispatch_key: str,
    attempt_no: int,
    occurred_at_ms: int,
    operation_identity: str,
    result: ExternalHttpTransportResult,
    retry_exhausted: bool,
    outcome: Success[Any] | BusinessReject | RetryableFailure | ContractViolation,
) -> EffectTransportResolution:
    typed_outcome = outcome.model_dump(mode="json")
    typed_result_hash = sha256(
        json.dumps(
            typed_outcome,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    evidence = {
        **_transport_evidence(result, operation_identity=operation_identity),
        "interpreted_outcome_kind": outcome.kind,
        "typed_result_hash": typed_result_hash,
        "typed_result_reference": f"runtime-intent-outcome:{dispatch_key}",
    }
    if isinstance(outcome, Success | BusinessReject):
        event_type = (
            EffectReducerEventType.SYNC_COMPLETED
            if isinstance(outcome, Success)
            else EffectReducerEventType.SYNC_REJECTED
        )
        reason_code = "SUCCESS" if isinstance(outcome, Success) else outcome.reason_code
        event = EffectReducerEvent(
            event_type=event_type,
            dispatch_key=dispatch_key,
            occurred_at_ms=occurred_at_ms,
            source_event_id=generated_effect_source_event_id(
                "wms-sync-terminal",
                dispatch_key,
                attempt_no,
                event_type.value,
                evidence,
            ),
            reason_code=reason_code,
            evidence_json={
                **evidence,
                "outcome_kind": outcome.kind,
                "outcome_code": reason_code,
            },
            terminal_outcome=typed_outcome,
        )
        return EffectTransportResolution(events=(event,))
    if isinstance(outcome, RetryableFailure) and outcome.error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS":
        if retry_exhausted:
            exhaustion_evidence = {
                **evidence,
                "outcome_kind": outcome.kind,
                "outcome_code": "WMS_SYNC_CONFIRMATION_BUDGET_EXHAUSTED",
            }
            return EffectTransportResolution(
                events=(
                    EffectReducerEvent(
                        event_type=EffectReducerEventType.TRANSPORT_AMBIGUOUS,
                        dispatch_key=dispatch_key,
                        occurred_at_ms=occurred_at_ms,
                        source_event_id=generated_effect_source_event_id(
                            "wms-sync-confirmation-exhausted",
                            dispatch_key,
                            attempt_no,
                            exhaustion_evidence,
                        ),
                        attempt_no=attempt_no,
                        reason_code="WMS_SYNC_CONFIRMATION_BUDGET_EXHAUSTED",
                        evidence_json=exhaustion_evidence,
                    ),
                    _reconciliation_event(
                        dispatch_key=dispatch_key,
                        attempt_no=attempt_no,
                        occurred_at_ms=occurred_at_ms,
                        reason_code="WMS_SYNC_CONFIRMATION_BUDGET_EXHAUSTED",
                        evidence_json=exhaustion_evidence,
                    ),
                )
            )
        return EffectTransportResolution(events=(), action=EffectTransportAction.RETRY_SAME_REQUEST)
    if isinstance(outcome, ContractViolation) and outcome.error_code == "IDEMPOTENCY_CONFLICT":
        return EffectTransportResolution(
            events=(
                EffectReducerEvent(
                    event_type=EffectReducerEventType.IDEMPOTENCY_CONFLICT,
                    dispatch_key=dispatch_key,
                    occurred_at_ms=occurred_at_ms,
                    source_event_id=generated_effect_source_event_id(
                        "wms-idempotency-conflict",
                        dispatch_key,
                        attempt_no,
                        evidence,
                    ),
                    reason_code="IDEMPOTENCY_CONFLICT",
                    evidence_json=evidence,
                ),
            )
        )
    reason_code = getattr(outcome, "error_code", None) or "WMS_SYNC_RESPONSE_INVALID"
    return EffectTransportResolution(
        events=(
            _reconciliation_event(
                dispatch_key=dispatch_key,
                attempt_no=attempt_no,
                occurred_at_ms=occurred_at_ms,
                reason_code=reason_code,
                evidence_json=evidence,
            ),
        )
    )


def _default_transport_resolution(
    *,
    dispatch_key: str,
    attempt_no: int,
    result: ExternalHttpTransportResult,
    retry_exhausted: bool,
    occurred_at_ms: int,
    operation_identity: str | None,
    reconcile_wms_ambiguous: bool = False,
) -> EffectTransportResolution:
    is_wms_effect = operation_identity in EFFECT_OPERATION_IDENTITIES
    wms_protocol_rejection = is_wms_effect and result.protocol_result is ExternalHttpProtocolResult.REJECTED
    expected_idempotency_code = _expected_wms_idempotency_code(
        result,
        protocol_rejection=wms_protocol_rejection,
    )
    if wms_protocol_rejection:
        # 异步 WMS submit 响应不能直接裁决业务 REJECTED；
        # HTTP 已收到请求，因此 transport 先记为 ACCEPTED，再按稳定协议码恢复。
        event_type = EffectReducerEventType.TRANSPORT_ACCEPTED
    elif result.protocol_result is ExternalHttpProtocolResult.REJECTED:
        event_type = EffectReducerEventType.TRANSPORT_REJECTED
    else:
        event_type = {
            ExternalHttpTransportOutcome.NOT_SENT: EffectReducerEventType.TRANSPORT_NOT_SENT,
            ExternalHttpTransportOutcome.ACCEPTED: EffectReducerEventType.TRANSPORT_ACCEPTED,
            ExternalHttpTransportOutcome.AMBIGUOUS: EffectReducerEventType.TRANSPORT_AMBIGUOUS,
        }[result.outcome]
    evidence = _transport_evidence(result, operation_identity=operation_identity)
    events = [
        EffectReducerEvent(
            event_type=event_type,
            dispatch_key=dispatch_key,
            occurred_at_ms=occurred_at_ms,
            source_event_id=generated_effect_source_event_id(
                "transport",
                dispatch_key,
                attempt_no,
                event_type.value,
                retry_exhausted,
                evidence,
            ),
            attempt_no=attempt_no,
            retry_exhausted=retry_exhausted and event_type is EffectReducerEventType.TRANSPORT_NOT_SENT,
            reason_code=result.error_code,
            evidence_json=evidence,
        )
    ]
    if result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS and (not is_wms_effect or reconcile_wms_ambiguous):
        events.append(
            _reconciliation_event(
                dispatch_key=dispatch_key,
                attempt_no=attempt_no,
                occurred_at_ms=occurred_at_ms,
                reason_code=result.error_code or "TRANSPORT_AMBIGUOUS",
                evidence_json=evidence,
            )
        )
    elif wms_protocol_rejection:
        if expected_idempotency_code is not None:
            if result.protocol_error_code == expected_idempotency_code and result.http_status_code == 422:
                events.append(
                    EffectReducerEvent(
                        event_type=EffectReducerEventType.IDEMPOTENCY_CONFLICT,
                        dispatch_key=dispatch_key,
                        occurred_at_ms=occurred_at_ms,
                        source_event_id=generated_effect_source_event_id(
                            "wms-idempotency-conflict",
                            dispatch_key,
                            attempt_no,
                            evidence,
                        ),
                        reason_code="IDEMPOTENCY_CONFLICT",
                        evidence_json=evidence,
                    )
                )
            elif result.protocol_error_code != expected_idempotency_code:
                events.append(
                    _reconciliation_event(
                        dispatch_key=dispatch_key,
                        attempt_no=attempt_no,
                        occurred_at_ms=occurred_at_ms,
                        reason_code="WMS_IDEMPOTENCY_PROTOCOL_CONFLICT",
                        evidence_json=evidence,
                    )
                )
        else:
            events.append(
                _reconciliation_event(
                    dispatch_key=dispatch_key,
                    attempt_no=attempt_no,
                    occurred_at_ms=occurred_at_ms,
                    reason_code="WMS_SUBMIT_PROTOCOL_REJECTED",
                    evidence_json=evidence,
                )
            )
    return EffectTransportResolution(events=tuple(events))


def _async_transport_resolution(
    *,
    dispatch_key: str,
    attempt_no: int,
    occurred_at_ms: int,
    operation_identity: str,
    result: ExternalHttpTransportResult,
    retry_exhausted: bool,
    outcome: Success[Any] | BusinessReject | ContractViolation,
) -> EffectTransportResolution:
    if isinstance(outcome, Success):
        ack = outcome.payload
        ack_hash = typed_wms_effect_ack_hash(ack)
        evidence = {
            **_transport_evidence(result, operation_identity=operation_identity),
            "interpreted_outcome_kind": outcome.kind,
            "typed_ack_hash": ack_hash,
            "typed_ack_reference": f"runtime-intent-outcome:{dispatch_key}",
            "outcome_kind": outcome.kind,
            "outcome_code": "WMS_ASYNC_ACK_ACCEPTED",
        }
        return EffectTransportResolution(
            events=(
                EffectReducerEvent(
                    event_type=EffectReducerEventType.TRANSPORT_ACCEPTED,
                    dispatch_key=dispatch_key,
                    occurred_at_ms=occurred_at_ms,
                    source_event_id=generated_effect_source_event_id(
                        "wms-async-ack",
                        dispatch_key,
                        attempt_no,
                        ack_hash,
                        evidence,
                    ),
                    attempt_no=attempt_no,
                    reason_code="WMS_ASYNC_ACK_ACCEPTED",
                    evidence_json=evidence,
                    terminal_outcome=outcome.model_dump(mode="json"),
                ),
            )
        )

    if isinstance(outcome, BusinessReject):
        return EffectTransportResolution(
            events=(
                build_wms_async_submit_reject_event(
                    dispatch_key=dispatch_key,
                    attempt_no=attempt_no,
                    occurred_at_ms=occurred_at_ms,
                    operation_identity=operation_identity,
                    result=result,
                    outcome=outcome,
                ),
            )
        )

    default_resolution = _default_transport_resolution(
        dispatch_key=dispatch_key,
        attempt_no=attempt_no,
        result=result,
        retry_exhausted=retry_exhausted,
        occurred_at_ms=occurred_at_ms,
        operation_identity=operation_identity,
    )
    if any(
        event.event_type
        in {
            EffectReducerEventType.IDEMPOTENCY_CONFLICT,
            EffectReducerEventType.RECONCILIATION_OPENED,
        }
        for event in default_resolution.events
    ):
        return default_resolution
    evidence = {
        **_transport_evidence(result, operation_identity=operation_identity),
        "interpreted_outcome_kind": outcome.kind,
        "outcome_kind": outcome.kind,
        "outcome_code": outcome.error_code,
    }
    return EffectTransportResolution(
        events=(
            *default_resolution.events,
            _reconciliation_event(
                dispatch_key=dispatch_key,
                attempt_no=attempt_no,
                occurred_at_ms=occurred_at_ms,
                reason_code=outcome.error_code,
                evidence_json=evidence,
            ),
        )
    )


def build_wms_async_submit_reject_event(
    *,
    dispatch_key: str,
    attempt_no: int,
    occurred_at_ms: int,
    operation_identity: str,
    result: ExternalHttpTransportResult,
    outcome: BusinessReject,
    additional_evidence: dict[str, Any] | None = None,
) -> EffectReducerEvent:
    """把已校验的 async submit 业务拒绝冻结为可重放的 hash-only event。"""

    typed_reject_hash = outcome.details.get("typed_reject_hash")
    if (
        not isinstance(typed_reject_hash, str)
        or len(typed_reject_hash) != 64
        or any(character not in "0123456789abcdef" for character in typed_reject_hash)
    ):
        raise ValueError("typed async submit reject requires the validated envelope hash")
    typed_outcome = outcome.model_dump(mode="json")
    evidence = {
        **(additional_evidence or {}),
        **_transport_evidence(result, operation_identity=operation_identity),
        "interpreted_outcome_kind": outcome.kind,
        "typed_reject_hash": typed_reject_hash,
        "typed_reject_reference": f"runtime-intent-outcome:{dispatch_key}",
        "outcome_kind": outcome.kind,
        "outcome_code": outcome.reason_code,
    }
    return EffectReducerEvent(
        event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
        dispatch_key=dispatch_key,
        occurred_at_ms=occurred_at_ms,
        source_event_id=generated_effect_source_event_id(
            "wms-async-submit-reject",
            dispatch_key,
            attempt_no,
            outcome.reason_code,
        ),
        attempt_no=attempt_no,
        reason_code=outcome.reason_code,
        evidence_json=evidence,
        terminal_outcome=typed_outcome,
    )


class EffectTransportBridge:
    """把已持久化 typed transport result 翻译为封闭 reducer event。"""

    def __init__(
        self,
        *,
        reducer: EffectReducer = effect_reducer,
        domain_projector: Any | None = None,
    ) -> None:
        self._reducer = reducer
        self._domain_projector = domain_projector

    def resolve_result(
        self,
        *,
        dispatch_key: str,
        attempt_no: int,
        result: ExternalHttpTransportResult,
        retry_exhausted: bool,
        occurred_at_ms: int,
        operation_identity: str | None = None,
        payload_json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        payload_hash: str | None = None,
    ) -> EffectTransportResolution:
        """先按静态 completion mode 解释，再生成互斥的 reducer event。"""

        operation = WMS_OPERATION_BY_IDENTITY.get(operation_identity) if operation_identity is not None else None
        if (
            operation is not None
            and operation.completion_mode is WmsCompletionMode.ASYNC_TASK
            and payload_json is not None
            and result.outcome is ExternalHttpTransportOutcome.ACCEPTED
        ):
            if not isinstance(idempotency_key, str) or not idempotency_key or not isinstance(payload_hash, str):
                outcome: Success[Any] | BusinessReject | ContractViolation = ContractViolation(
                    error_code="WMS_ASYNC_FROZEN_REQUEST_INVALID",
                    message="async ACK resolution requires the frozen idempotency key and payload fingerprint",
                )
            else:
                outcome = interpret_async_effect_ack_response(
                    operation,
                    payload_json,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    transport_result=result,
                )
            return _async_transport_resolution(
                dispatch_key=dispatch_key,
                attempt_no=attempt_no,
                occurred_at_ms=occurred_at_ms,
                operation_identity=operation.identity,
                result=result,
                retry_exhausted=retry_exhausted,
                outcome=outcome,
            )
        if (
            operation is not None
            and operation.completion_mode is WmsCompletionMode.SYNC_RESULT
            and payload_json is not None
        ):
            if result.outcome is not ExternalHttpTransportOutcome.ACCEPTED:
                if result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS and not retry_exhausted:
                    return EffectTransportResolution(events=(), action=EffectTransportAction.RETRY_SAME_REQUEST)
                return _default_transport_resolution(
                    dispatch_key=dispatch_key,
                    attempt_no=attempt_no,
                    result=result,
                    retry_exhausted=retry_exhausted,
                    occurred_at_ms=occurred_at_ms,
                    operation_identity=operation_identity,
                    reconcile_wms_ambiguous=True,
                )
            try:
                request = operation.request_model.model_validate(payload_json)
            except (TypeError, ValueError):
                return EffectTransportResolution(
                    events=(
                        _reconciliation_event(
                            dispatch_key=dispatch_key,
                            attempt_no=attempt_no,
                            occurred_at_ms=occurred_at_ms,
                            reason_code="WMS_SYNC_FROZEN_REQUEST_INVALID",
                            evidence_json=_transport_evidence(result, operation_identity=operation_identity),
                        ),
                    )
                )
            outcome = interpret_sync_effect_response(operation, request, result)
            return _sync_transport_resolution(
                dispatch_key=dispatch_key,
                attempt_no=attempt_no,
                occurred_at_ms=occurred_at_ms,
                operation_identity=operation_identity,
                result=result,
                retry_exhausted=retry_exhausted,
                outcome=outcome,
            )
        return _default_transport_resolution(
            dispatch_key=dispatch_key,
            attempt_no=attempt_no,
            result=result,
            retry_exhausted=retry_exhausted,
            occurred_at_ms=occurred_at_ms,
            operation_identity=operation_identity,
        )

    async def record_result(
        self,
        db: Any,
        *,
        dispatch_key: str,
        attempt_no: int,
        result: ExternalHttpTransportResult,
        retry_exhausted: bool,
        occurred_at_ms: int,
        operation_identity: str | None = None,
        payload_json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        payload_hash: str | None = None,
        resolution: EffectTransportResolution | None = None,
    ) -> tuple[Any, ...]:
        resolved = resolution or self.resolve_result(
            dispatch_key=dispatch_key,
            attempt_no=attempt_no,
            result=result,
            retry_exhausted=retry_exhausted,
            occurred_at_ms=occurred_at_ms,
            operation_identity=operation_identity,
            payload_json=payload_json,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        barrier_group = None
        barrier_service = None
        if operation_identity in WMS_SYNC_OBLIGATION_OPERATION_IDENTITIES and any(
            event.event_type is EffectReducerEventType.SYNC_COMPLETED for event in resolved.events
        ):
            from src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service import (
                wms_putaway_sync_barrier_service,
            )

            barrier_service = wms_putaway_sync_barrier_service
            barrier_group = await barrier_service.lock_group_for_dispatch(db, dispatch_key=dispatch_key)
            if barrier_group is None:
                raise RuntimeError("E03/E07 dispatch must resolve to a WMS sync barrier group")
        reductions = []
        operation = WMS_OPERATION_BY_IDENTITY.get(operation_identity) if operation_identity is not None else None
        for event in resolved.events:
            reduction = await self._reducer.reduce(db, event, require_intent=False)
            reductions.append(reduction)
            if (
                self._domain_projector is not None
                and operation is not None
                and operation.domain_projection_kind is not None
                and event.event_type is EffectReducerEventType.ASYNC_SUBMIT_REJECTED
            ):
                if not isinstance(payload_json, dict):
                    raise RuntimeError("WMS domain projection requires frozen request payload")
                await self._domain_projector.project_event(
                    db,
                    operation=operation,
                    request_payload=payload_json,
                    event=event,
                    reduction=reduction,
                )
        if barrier_service is not None and barrier_group is not None:
            await barrier_service.evaluate_dispatch(
                db,
                dispatch_key=dispatch_key,
                locked_group=barrier_group,
            )
        return tuple(reductions)


class EffectCallbackBridge:
    """业务 adapter 已完成 typed 归一化后的 callback 入口。"""

    def __init__(self, *, reducer: EffectReducer = effect_reducer) -> None:
        self._reducer = reducer

    async def record(
        self,
        db: Any,
        *,
        dispatch_key: str,
        outcome: EffectCallbackOutcome,
        occurred_at_ms: int,
        source_event_id: str,
        evidence_json: dict[str, Any],
        reason_code: str | None = None,
    ) -> Any:
        event_type = {
            EffectCallbackOutcome.ACCEPTED: EffectReducerEventType.CALLBACK_ACCEPTED,
            EffectCallbackOutcome.COMPLETED: EffectReducerEventType.CALLBACK_COMPLETED,
            EffectCallbackOutcome.REJECTED: EffectReducerEventType.CALLBACK_REJECTED,
        }[outcome]
        return await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=event_type,
                dispatch_key=dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                reason_code=reason_code,
                evidence_json=evidence_json,
            ),
        )


class EffectReconciliationBridge:
    """对账 policy 唯一可用的 OPEN/RESOLVED event 入口。"""

    def __init__(self, *, reducer: EffectReducer = effect_reducer) -> None:
        self._reducer = reducer

    async def open(
        self,
        db: Any,
        *,
        dispatch_key: str,
        occurred_at_ms: int,
        reason_code: str,
        evidence_json: dict[str, Any],
        source_event_id: str | None = None,
    ) -> Any:
        return await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.RECONCILIATION_OPENED,
                dispatch_key=dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                reason_code=reason_code,
                evidence_json=evidence_json,
            ),
        )

    async def resolve(
        self,
        db: Any,
        *,
        dispatch_key: str,
        occurred_at_ms: int,
        resolution: RuntimeIntentStatus | None,
        reason_code: str,
        evidence_json: dict[str, Any],
        source_event_id: str,
        obligation_resolution: WmsSyncObligationResolution | None = None,
    ) -> Any:
        if not source_event_id.strip():
            raise ValueError("RECONCILIATION_RESOLVED requires a stable source_event_id")
        barrier_group = None
        barrier_service = None
        if obligation_resolution is not None:
            from src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service import (
                wms_putaway_sync_barrier_service,
            )

            barrier_service = wms_putaway_sync_barrier_service
            barrier_group = await barrier_service.lock_group_for_dispatch(db, dispatch_key=dispatch_key)
            if barrier_group is None:
                raise RuntimeError("typed E03/E07 resolution must resolve to a WMS sync barrier group")
        reduction = await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.RECONCILIATION_RESOLVED,
                dispatch_key=dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                resolution=resolution,
                obligation_resolution=obligation_resolution,
                reason_code=reason_code,
                evidence_json=evidence_json,
            ),
        )
        if barrier_service is not None and barrier_group is not None:
            await barrier_service.evaluate_dispatch(
                db,
                dispatch_key=dispatch_key,
                locked_group=barrier_group,
            )
        return reduction

    async def record_idempotency_conflict(
        self,
        db: Any,
        *,
        dispatch_key: str,
        occurred_at_ms: int,
        evidence_json: dict[str, Any],
        source_event_id: str | None = None,
    ) -> Any:
        return await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.IDEMPOTENCY_CONFLICT,
                dispatch_key=dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                reason_code="IDEMPOTENCY_CONFLICT",
                evidence_json=evidence_json,
            ),
        )


# 组合根延迟导入，避免 services.__init__ 在 bridge 定义完成前形成循环依赖。
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (  # noqa: E402
    wms_fulfillment_domain_projector,
)

effect_transport_bridge = EffectTransportBridge(domain_projector=wms_fulfillment_domain_projector)
effect_callback_bridge = EffectCallbackBridge()
effect_reconciliation_bridge = EffectReconciliationBridge()


__all__ = [
    "EffectCallbackBridge",
    "EffectCallbackOutcome",
    "EffectReconciliationBridge",
    "EffectTransportAction",
    "EffectTransportBridge",
    "EffectTransportResolution",
    "build_wms_async_submit_reject_event",
    "effect_callback_bridge",
    "effect_reconciliation_bridge",
    "effect_transport_bridge",
]
