"""Transport、callback 与 reconciliation 到唯一 EFFECT reducer 的 typed bridge。"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    generated_effect_source_event_id,
)
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer, effect_reducer
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.ports.effect_status import WMS_EFFECT_OPERATION_IDENTITIES

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus


class EffectCallbackOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class EffectTransportBridge:
    """把已持久化 typed transport result 翻译为封闭 reducer event。"""

    def __init__(self, *, reducer: EffectReducer = effect_reducer) -> None:
        self._reducer = reducer

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
    ) -> tuple[Any, ...]:
        is_wms_effect = operation_identity in WMS_EFFECT_OPERATION_IDENTITIES
        wms_protocol_rejection = is_wms_effect and result.protocol_result is ExternalHttpProtocolResult.REJECTED
        wms_idempotency_response = wms_protocol_rejection and result.http_status_code in {409, 422}
        if wms_protocol_rejection:
            # WMS submit 响应不能直接裁决业务 REJECTED；只有 status query 拥有终态裁决权。
            # HTTP 已收到请求，因此 transport 先记为 ACCEPTED，再按稳定协议码进入冲突或对账恢复。
            event_type = EffectReducerEventType.TRANSPORT_ACCEPTED
        elif result.protocol_result is ExternalHttpProtocolResult.REJECTED:
            event_type = EffectReducerEventType.TRANSPORT_REJECTED
        else:
            event_type = {
                ExternalHttpTransportOutcome.NOT_SENT: EffectReducerEventType.TRANSPORT_NOT_SENT,
                ExternalHttpTransportOutcome.ACCEPTED: EffectReducerEventType.TRANSPORT_ACCEPTED,
                ExternalHttpTransportOutcome.AMBIGUOUS: EffectReducerEventType.TRANSPORT_AMBIGUOUS,
            }[result.outcome]
        transport_evidence = result.evidence_json()
        if operation_identity is not None:
            transport_evidence = {**transport_evidence, "operation_identity": operation_identity}
        source_event_id = generated_effect_source_event_id(
            "transport",
            dispatch_key,
            attempt_no,
            event_type.value,
            retry_exhausted,
            transport_evidence,
        )
        events = [
            EffectReducerEvent(
                event_type=event_type,
                dispatch_key=dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                attempt_no=attempt_no,
                retry_exhausted=retry_exhausted,
                reason_code=result.error_code,
                evidence_json=transport_evidence,
            )
        ]
        if result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS and not is_wms_effect:
            # authored WMS EFFECT 已有冻结 status binding 和持久化查询预算：
            # transport 不明确时保持 UNKNOWN，由 typed status 确认闭环；
            # 非 WMS EFFECT 仍立即进入人工对账。
            events.append(
                EffectReducerEvent(
                    event_type=EffectReducerEventType.RECONCILIATION_OPENED,
                    dispatch_key=dispatch_key,
                    occurred_at_ms=occurred_at_ms,
                    source_event_id=generated_effect_source_event_id(
                        "transport-reconciliation",
                        dispatch_key,
                        attempt_no,
                        event_type.value,
                        transport_evidence,
                    ),
                    reason_code=result.error_code or "TRANSPORT_AMBIGUOUS",
                    evidence_json=transport_evidence,
                )
            )
        elif wms_protocol_rejection:
            if wms_idempotency_response:
                expected_code = {
                    409: "IDEMPOTENCY_REQUEST_IN_PROGRESS",
                    422: "IDEMPOTENCY_CONFLICT",
                }[result.http_status_code]
                if result.protocol_error_code == expected_code and result.http_status_code == 422:
                    events.append(
                        EffectReducerEvent(
                            event_type=EffectReducerEventType.IDEMPOTENCY_CONFLICT,
                            dispatch_key=dispatch_key,
                            occurred_at_ms=occurred_at_ms,
                            source_event_id=generated_effect_source_event_id(
                                "wms-idempotency-conflict",
                                dispatch_key,
                                attempt_no,
                                transport_evidence,
                            ),
                            reason_code="IDEMPOTENCY_CONFLICT",
                            evidence_json=transport_evidence,
                        )
                    )
                elif result.protocol_error_code != expected_code:
                    events.append(
                        EffectReducerEvent(
                            event_type=EffectReducerEventType.RECONCILIATION_OPENED,
                            dispatch_key=dispatch_key,
                            occurred_at_ms=occurred_at_ms,
                            source_event_id=generated_effect_source_event_id(
                                "wms-idempotency-protocol-conflict",
                                dispatch_key,
                                attempt_no,
                                transport_evidence,
                            ),
                            reason_code="WMS_IDEMPOTENCY_PROTOCOL_CONFLICT",
                            evidence_json=transport_evidence,
                        )
                    )
            else:
                events.append(
                    EffectReducerEvent(
                        event_type=EffectReducerEventType.RECONCILIATION_OPENED,
                        dispatch_key=dispatch_key,
                        occurred_at_ms=occurred_at_ms,
                        source_event_id=generated_effect_source_event_id(
                            "wms-submit-protocol-rejected",
                            dispatch_key,
                            attempt_no,
                            transport_evidence,
                        ),
                        reason_code="WMS_SUBMIT_PROTOCOL_REJECTED",
                        evidence_json=transport_evidence,
                    )
                )
        return tuple([await self._reducer.reduce(db, event, require_intent=False) for event in events])


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
        resolution: RuntimeIntentStatus,
        reason_code: str,
        evidence_json: dict[str, Any],
        source_event_id: str,
    ) -> Any:
        if not source_event_id.strip():
            raise ValueError("RECONCILIATION_RESOLVED requires a stable source_event_id")
        return await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.RECONCILIATION_RESOLVED,
                dispatch_key=dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                resolution=resolution,
                reason_code=reason_code,
                evidence_json=evidence_json,
            ),
        )

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


effect_transport_bridge = EffectTransportBridge()
effect_callback_bridge = EffectCallbackBridge()
effect_reconciliation_bridge = EffectReconciliationBridge()


__all__ = [
    "EffectCallbackBridge",
    "EffectCallbackOutcome",
    "EffectReconciliationBridge",
    "EffectTransportBridge",
    "effect_callback_bridge",
    "effect_reconciliation_bridge",
    "effect_transport_bridge",
]
