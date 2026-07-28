"""WMS EFFECT 状态确认终审可靠性回归。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge, EffectTransportBridge
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCaseStatus
from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusClaim
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.services.outbox_engine import SystemOutboxEngine
from src.app.wms_integration.ports.effect_status import WmsEffectStatus, WmsEffectStatusSnapshot
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import (
    NOW,
    _claim,
    _Db,
    _FailingPort,
    _Port,
    _ReconciliationBridge,
    _Reducer,
    _Repository,
    _settings,
    _snapshot,
)


class _PersistentReducerRepository:
    def __init__(self, intent: Any) -> None:
        self.intent = intent
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

    async def list_resolved_cases_for_update(self, _db: Any, dispatch_key: str) -> tuple[Any, ...]:
        return tuple(
            case
            for case in reversed(self.cases)
            if case.dispatch_key == dispatch_key and case.status is ReconciliationCaseStatus.RESOLVED
        )

    def add_case(self, _db: Any, case: Any) -> None:
        self.cases.append(case)


class _ClaimableRepository(_Repository):
    async def claim_by_dispatch_key(self, _db: Any, **_kwargs: Any) -> Any:
        if self.claim.intent.effect_status not in {
            RuntimeIntentStatus.PROPOSED,
            RuntimeIntentStatus.ACCEPTED,
            RuntimeIntentStatus.UNKNOWN,
        }:
            return None
        return self.claim


@pytest.mark.asyncio
async def test_wms_ambiguous_transport_is_enqueued_and_reaches_typed_terminal_without_open_case() -> None:
    claim = _claim()
    claim.intent.capability_key = "wms.fulfillment.request_rack_supply"
    claim.intent.capability_contract_version = "v1"
    reducer_repository = _PersistentReducerRepository(claim.intent)
    reducer = EffectReducer(repository=reducer_repository)
    db = _Db()

    await EffectTransportBridge(reducer=reducer).record_result(
        db,
        dispatch_key=claim.intent.dispatch_key,
        attempt_no=1,
        result=ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
        retry_exhausted=False,
        occurred_at_ms=1_800_000_000_000,
        operation_identity=claim.outbox.operation_identity,
    )

    queue = SimpleNamespace(dispatch_keys=[])
    queue.enqueue_wms_effect_status = lambda *, dispatch_key: queue.dispatch_keys.append(dispatch_key)
    SystemOutboxEngine(task_queue_gateway=queue)._enqueue_wms_effect_status_if_needed(
        outbox=claim.outbox,
        result=ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
    )
    result = await WmsEffectStatusService(
        repository=_ClaimableRepository(claim),
        reducer=reducer,
        reconciliation_bridge=EffectReconciliationBridge(reducer=reducer),
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.COMPLETED), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert queue.dispatch_keys == [claim.intent.dispatch_key]
    assert result.outcome == WmsEffectStatus.COMPLETED.value
    assert claim.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert [item["event_type"] for item in claim.intent.outcome_history_json] == [
        EffectReducerEventType.TRANSPORT_AMBIGUOUS.value,
        EffectReducerEventType.STATUS_COMPLETED.value,
    ]
    assert reducer_repository.cases == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [WmsEffectStatus.ACCEPTED, WmsEffectStatus.PROCESSING])
async def test_non_terminal_snapshot_at_exact_attempt_budget_opens_stable_reconciliation(
    state: WmsEffectStatus,
) -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_count = 3
    db = _Db()
    reconciliation = _ReconciliationBridge()

    result = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(state), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_terminal_snapshot_wins_on_exact_attempt_and_age_budget_boundary() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_count = 3
    claim.intent.status_check_started_at = (NOW - timedelta(seconds=300)).replace(tzinfo=UTC)
    db = _Db()
    reconciliation = _ReconciliationBridge()

    result = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.COMPLETED), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == WmsEffectStatus.COMPLETED.value
    assert claim.intent.status_check_after is None
    assert reconciliation.calls == []


@pytest.mark.asyncio
async def test_persisted_confirmation_age_survives_restart_and_exhausts_on_non_terminal_snapshot() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_started_at = (NOW - timedelta(seconds=300)).replace(tzinfo=UTC)
    db = _Db()
    reconciliation = _ReconciliationBridge()

    restarted_service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.PROCESSING), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    result = await restarted_service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"
    assert claim.intent.status_check_started_at.tzinfo is UTC
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_retryable_query_failure_at_exact_attempt_budget_stops_scheduling() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_count = 3
    db = _Db()
    reconciliation = _ReconciliationBridge()
    error = RuntimeError("circuit open")
    error.failure = SimpleNamespace(reason_code="WMS_CIRCUIT_OPEN", retryable=True)  # type: ignore[attr-defined]

    result = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _FailingPort(error, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_retry_after_beyond_remaining_confirmation_age_stops_scheduling() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_started_at = NOW - timedelta(seconds=299)
    db = _Db()
    reconciliation = _ReconciliationBridge()
    error = RuntimeError("provider rate limit")
    error.failure = SimpleNamespace(  # type: ignore[attr-defined]
        reason_code="WMS_RATE_LIMITED",
        retryable=True,
        retry_after_seconds=20.0,
    )

    result = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _FailingPort(error, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_not_found_at_attempt_budget_never_consumes_resubmit_budget() -> None:
    claim = _claim()
    claim.intent.status_check_count = 3
    claim.intent.status_check_started_at = NOW - timedelta(seconds=61)
    db = _Db()
    repository = _Repository(claim)
    reconciliation = _ReconciliationBridge()
    resubmit_calls = 0

    async def resubmit(_claim: Any) -> ExternalHttpTransportResult:
        nonlocal resubmit_calls
        resubmit_calls += 1
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )

    not_found = WmsEffectStatusSnapshot.not_found(WmsEffectStatusService._build_request(claim))
    result = await WmsEffectStatusService(
        repository=repository,
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(not_found, db),
        resubmit_dispatcher=resubmit,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"
    assert resubmit_calls == 0
    assert claim.intent.status_resubmit_count == 0
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_stale_snapshot_at_attempt_budget_stops_scheduling() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_count = 3
    claim.intent.status_source_version = 8
    db = _Db()
    reconciliation = _ReconciliationBridge()

    result = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: (
            lambda: _Port(_snapshot(WmsEffectStatus.PROCESSING, source_version=7), db)
        ),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"
    assert claim.intent.status_check_after is None


def _batch_claim(suffix: str) -> Any:
    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    dispatch_key = f"dispatch-{suffix}"
    idempotency_key = f"idem-{suffix}"
    claim.intent.dispatch_key = dispatch_key
    claim.intent.idempotency_key = idempotency_key
    claim.intent.status_check_count = 0
    claim.intent.status_check_lease_token = None
    claim.intent.status_check_lease_until = None
    claim.outbox.dispatch_key = dispatch_key
    claim.outbox.idempotency_key = idempotency_key
    claim.outbox.payload_json = {
        **claim.outbox.payload_json,
        "dispatch_key": dispatch_key,
        "station_code": f"STATION-{suffix}",
        "rack_type": "FLOW_RACK",
        "demand_generation": 1,
    }
    return WmsEffectStatusClaim(intent=claim.intent, outbox=claim.outbox, lease_token="")


class _MultiClaimRepository:
    def __init__(self, claims: list[Any]) -> None:
        self.claims = claims
        self.claimed_order: list[str] = []
        self.claim_limits: list[int] = []

    async def claim_due_batch(
        self,
        _db: Any,
        *,
        now: datetime,
        lease_seconds: float,
        limit: int,
    ) -> tuple[Any, ...]:
        self.claim_limits.append(limit)
        selected: list[Any] = []
        for index, claim in enumerate(self.claims):
            if len(selected) >= limit:
                break
            intent = claim.intent
            if intent.status_check_lease_token is not None or intent.status_check_after > now:
                continue
            token = f"lease-{intent.dispatch_key}-{len(self.claimed_order) + 1}"
            intent.status_check_count += 1
            intent.status_check_lease_token = token
            intent.status_check_lease_until = now + timedelta(seconds=lease_seconds)
            current = WmsEffectStatusClaim(intent=intent, outbox=claim.outbox, lease_token=token)
            self.claims[index] = current
            self.claimed_order.append(intent.dispatch_key)
            selected.append(current)
        return tuple(selected)

    async def get_claim_for_update(
        self,
        _db: Any,
        *,
        dispatch_key: str,
        lease_token: str,
    ) -> Any:
        return next(
            (
                claim
                for claim in self.claims
                if claim.intent.dispatch_key == dispatch_key and claim.intent.status_check_lease_token == lease_token
            ),
            None,
        )

    async def release_claim(
        self,
        _db: Any,
        *,
        claim: Any,
        status_check_after: datetime | None,
    ) -> bool:
        claim.intent.status_check_after = status_check_after
        claim.intent.status_check_lease_token = None
        claim.intent.status_check_lease_until = None
        return True


@pytest.mark.asyncio
async def test_batch_claims_each_item_only_when_its_network_call_is_ready() -> None:
    claims = [_batch_claim("001"), _batch_claim("002")]
    repository = _MultiClaimRepository(claims)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    query_order: list[str] = []

    class BlockingPort:
        async def query_status(self, request: Any) -> WmsEffectStatusSnapshot:
            dispatch_key = request.request_payload["dispatch_key"]
            query_order.append(dispatch_key)
            if dispatch_key == "dispatch-001":
                first_started.set()
                await release_first.wait()
            return WmsEffectStatusSnapshot(
                operation_identity=request.operation_identity,
                idempotency_key=request.idempotency_key,
                state=WmsEffectStatus.ACCEPTED,
                provider_reference=f"provider-{dispatch_key}",
                updated_at=NOW.replace(tzinfo=UTC),
                source_version=1,
            )

    def service() -> WmsEffectStatusService:
        return WmsEffectStatusService(
            repository=repository,
            reducer=_Reducer(),
            reconciliation_bridge=_ReconciliationBridge(),
            port_factory_builder=lambda _binding: BlockingPort,
            settings_source=_settings(),
            now=lambda: NOW,
            jitter=lambda _upper: 0.0,
        )

    first_worker = asyncio.create_task(service().check_due_batch(_Db(), limit=2))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    claimed_while_first_http_blocked = list(repository.claimed_order)
    second_worker_results = await service().check_due_batch(_Db(), limit=2)
    release_first.set()
    first_worker_results = await first_worker

    assert claimed_while_first_http_blocked == ["dispatch-001"]
    assert repository.claim_limits and set(repository.claim_limits) == {1}
    assert [result.dispatch_key for result in first_worker_results] == ["dispatch-001"]
    assert [result.dispatch_key for result in second_worker_results] == ["dispatch-002"]
    assert sorted(query_order) == ["dispatch-001", "dispatch-002"]
