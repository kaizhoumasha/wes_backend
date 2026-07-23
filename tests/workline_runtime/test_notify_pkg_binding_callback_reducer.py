"""`notify_pkg_binding` callback 乱序、重复、矛盾与 timeout-success 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_bridges import (
    EffectReconciliationBridge,
    EffectTransportBridge,
)
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationResult


class _Db:
    async def flush(self) -> None:
        return None


class _Repository:
    def __init__(self) -> None:
        self.intent = SimpleNamespace(
            id=17,
            dispatch_key="wms-notify-pkg-binding:WMS:PKG-001:PALLET-001",
            effect_status=RuntimeIntentStatus.PROPOSED,
            outcome_kind=None,
            outcome_code=None,
            outcome_json={},
            outcome_history_json=[],
            effect_updated_at_ms=None,
        )
        self.cases: list[Any] = []

    async def get_intent_for_update(self, _db: Any, dispatch_key: str) -> Any | None:
        return self.intent if dispatch_key == self.intent.dispatch_key else None

    async def get_open_case_for_update(self, _db: Any, dispatch_key: str) -> Any | None:
        return next(
            (
                case
                for case in reversed(self.cases)
                if case.dispatch_key == dispatch_key and case.status is ReconciliationCaseStatus.OPEN
            ),
            None,
        )

    def add_case(self, _db: Any, case: Any) -> None:
        self.cases.append(case)


def _result(*, accepted: bool = True, reason_code: str | None = None) -> NotifyPackageBindingOperationResult:
    return NotifyPackageBindingOperationResult(
        dispatch_key="wms-notify-pkg-binding:WMS:PKG-001:PALLET-001",
        package_id="PKG-001",
        pallet_id="PALLET-001",
        accepted=accepted,
        bound_at="2026-07-23T10:00:00Z" if accepted else None,
        reason_code=reason_code,
        source_version="wms:v12",
    )


def _adapter(repository: _Repository):
    from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.callback_adapter import (
        NotifyPackageBindingCallbackAdapter,
    )

    reducer = EffectReducer(repository=repository)
    return NotifyPackageBindingCallbackAdapter(
        bridge=__import__(
            "src.app.runtime.orchestration.effect_bridges",
            fromlist=["EffectCallbackBridge"],
        ).EffectCallbackBridge(reducer=reducer)
    ), reducer


@pytest.mark.asyncio
async def test_callback_before_response_and_duplicate_are_monotonic() -> None:
    repository = _Repository()
    adapter, reducer = _adapter(repository)

    await adapter.record(_Db(), result=_result(), occurred_at_ms=1100, source_event_id="callback-1")
    await EffectTransportBridge(reducer=reducer).record_result(
        _Db(),
        dispatch_key=repository.intent.dispatch_key,
        attempt_no=1,
        result=ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        ),
        retry_exhausted=False,
        occurred_at_ms=900,
    )
    await adapter.record(_Db(), result=_result(), occurred_at_ms=1200, source_event_id="callback-duplicate")

    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert [item["event_type"] for item in repository.intent.outcome_history_json] == [
        "CALLBACK_COMPLETED",
        "TRANSPORT_ACCEPTED",
        "CALLBACK_COMPLETED",
    ]


@pytest.mark.asyncio
async def test_late_contradictory_callback_only_opens_case_and_preserves_terminal() -> None:
    repository = _Repository()
    adapter, _reducer = _adapter(repository)

    await adapter.record(_Db(), result=_result(), occurred_at_ms=1000, source_event_id="callback-success")
    contradiction = await adapter.record(
        _Db(),
        result=_result(accepted=False, reason_code="PALLET_LOCKED"),
        occurred_at_ms=1200,
        source_event_id="callback-late-reject",
    )

    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert contradiction.contradiction is True
    assert len(repository.cases) == 1
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN


@pytest.mark.asyncio
async def test_timeout_then_success_callback_is_resolved_explicitly_without_terminal_rewrite() -> None:
    repository = _Repository()
    adapter, reducer = _adapter(repository)
    db = _Db()

    await EffectTransportBridge(reducer=reducer).record_result(
        db,
        dispatch_key=repository.intent.dispatch_key,
        attempt_no=1,
        result=ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
        retry_exhausted=False,
        occurred_at_ms=1000,
    )
    await adapter.record(db, result=_result(), occurred_at_ms=1100, source_event_id="callback-after-timeout")

    assert repository.intent.effect_status is RuntimeIntentStatus.RECONCILING
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN

    await EffectReconciliationBridge(reducer=reducer).resolve(
        db,
        dispatch_key=repository.intent.dispatch_key,
        occurred_at_ms=1200,
        resolution=RuntimeIntentStatus.COMPLETED,
        reason_code="REMOTE_SUCCESS_CONFIRMED",
        evidence_json={"source_event_id": "callback-after-timeout"},
    )

    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert repository.cases[0].status is ReconciliationCaseStatus.RESOLVED
