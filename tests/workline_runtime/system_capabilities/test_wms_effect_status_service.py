"""WMS EFFECT 状态查询 orchestration、lease fencing 与版本归并合同。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.ports.effect_status import (
    NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
    WMS_EFFECT_OPERATION_IDENTITIES,
    WmsEffectStatus,
    WmsEffectStatusSnapshot,
    build_wms_effect_status_binding,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationResult

try:
    from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusClaim
    from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
except ImportError as exc:
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None


NOW = datetime(2026, 7, 24, 12, 0, 0)


def _require_status_service() -> None:
    assert _IMPORT_ERROR is None, f"WMS EFFECT status service 尚未实现: {_IMPORT_ERROR}"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        APP_ENV="test",
        WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v2",
        WMS_EFFECT_STATUS_URL="https://wms.example/northbound/operations/status",
        WMS_EFFECT_STATUS_TIMEOUT_SECONDS=2.0,
        WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES=4096,
        WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS=10.0,
        WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS=2.0,
        WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS=30.0,
        WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS=3,
        WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=300.0,
        WES_EFFECT_NOT_FOUND_GRACE_SECONDS=60.0,
    )


def test_default_status_port_factory_receives_service_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.wms_integration import runtime_factory

    configured = _settings()
    captured: dict[str, Any] = {}

    def capture_factory(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return lambda: object()

    monkeypatch.setattr(runtime_factory, "build_effect_status_query_port_factory", capture_factory)
    service = WmsEffectStatusService(settings_source=configured)
    binding = service._load_binding(_claim())

    service._default_port_factory_builder(binding)

    assert captured["settings_source"] is configured


def _claim(*, status: RuntimeIntentStatus = RuntimeIntentStatus.PROPOSED) -> Any:
    binding = build_wms_effect_status_binding(settings_source=_settings()).as_persisted()
    intent = SimpleNamespace(
        id=17,
        dispatch_key="dispatch-001",
        idempotency_key="idem-001",
        effect_status=status,
        status_check_started_at=NOW,
        status_check_after=NOW,
        status_check_count=1,
        status_resubmit_count=0,
        status_source_version=None,
        status_check_lease_token="lease-001",
        status_check_lease_until=NOW + timedelta(seconds=10),
        status_binding_snapshot_json=binding["snapshot"],
        status_binding_snapshot_hash=binding["snapshot_hash"],
        outcome_history_json=[],
        outcome_kind=None,
        outcome_code=None,
        outcome_json={},
        effect_updated_at_ms=None,
    )
    outbox = SimpleNamespace(
        id=31,
        dispatch_key="dispatch-001",
        idempotency_key="idem-001",
        operation_identity=NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
        provider_profile_identity=binding["snapshot"]["provider_profile_identity"],
        provider_profile_hash=binding["snapshot"]["provider_profile_hash"],
        payload_json={
            "dispatch_key": "dispatch-001",
            "package_id": "PKG-001",
            "pallet_id": "PALLET-001",
            "station_code": "ST-001",
        },
        status="SENT",
        attempt_count=4,
        next_retry_at=NOW + timedelta(minutes=5),
    )
    return WmsEffectStatusClaim(intent=intent, outbox=outbox, lease_token="lease-001")


class _Db:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        self.flushes += 1


class _Repository:
    def __init__(self, claim: Any, *, fence_writeback: bool = False) -> None:
        self.claim = claim
        self.fence_writeback = fence_writeback
        self.released = 0
        self.reserved = 0
        self.batch_claimed = False

    async def claim_by_dispatch_key(self, _db: Any, **_kwargs: Any) -> Any:
        return self.claim

    async def claim_due_batch(self, _db: Any, **_kwargs: Any) -> tuple[Any, ...]:
        if self.batch_claimed:
            return ()
        self.batch_claimed = True
        return (self.claim,)

    async def get_claim_for_update(self, _db: Any, **_kwargs: Any) -> Any:
        return None if self.fence_writeback else self.claim

    async def release_claim(
        self,
        _db: Any,
        *,
        claim: Any,
        status_check_after: datetime | None,
    ) -> bool:
        self.released += 1
        claim.intent.status_check_after = status_check_after
        claim.intent.status_check_lease_token = None
        claim.intent.status_check_lease_until = None
        return True

    async def reserve_resubmit(self, _db: Any, *, claim: Any) -> bool:
        if claim.intent.status_resubmit_count:
            return False
        claim.intent.status_resubmit_count = 1
        self.reserved += 1
        return True


class _Reducer:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def reduce(self, _db: Any, event: Any, **_kwargs: Any) -> SimpleNamespace:
        self.events.append(event)
        return SimpleNamespace(state_changed=True)


class _ReconciliationBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def open(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(state_changed=True)


class _Port:
    def __init__(self, snapshot: WmsEffectStatusSnapshot, db: _Db) -> None:
        self.snapshot = snapshot
        self.db = db
        self.requests: list[Any] = []

    async def query_status(self, request: Any) -> WmsEffectStatusSnapshot:
        assert self.db.commits == 1, "claim 必须先独立提交，HTTP 才能开始"
        self.requests.append(request)
        return self.snapshot


class _FailingPort:
    def __init__(self, error: Exception, db: _Db) -> None:
        self.error = error
        self.db = db

    async def query_status(self, _request: Any) -> WmsEffectStatusSnapshot:
        assert self.db.commits == 1, "claim 必须先独立提交，HTTP 才能开始"
        raise self.error


def _snapshot(state: WmsEffectStatus, *, source_version: int = 7) -> WmsEffectStatusSnapshot:
    visible = {
        "provider_reference": "wms-effect-001",
        "updated_at": datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        "source_version": source_version,
    }
    if state is WmsEffectStatus.COMPLETED:
        return WmsEffectStatusSnapshot(
            operation_identity=NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
            idempotency_key="idem-001",
            state=state,
            result=NotifyPackageBindingOperationResult(
                dispatch_key="dispatch-001",
                package_id="PKG-001",
                pallet_id="PALLET-001",
                accepted=True,
                bound_at="2026-07-24T12:00:00Z",
                source_version=str(source_version),
            ),
            **visible,
        )
    if state is WmsEffectStatus.REJECTED:
        return WmsEffectStatusSnapshot(
            operation_identity=NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
            idempotency_key="idem-001",
            state=state,
            reason_code="WMS_BUSINESS_REJECTED",
            **visible,
        )
    return WmsEffectStatusSnapshot(
        operation_identity=NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
        idempotency_key="idem-001",
        state=state,
        **visible,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [WmsEffectStatus.ACCEPTED, WmsEffectStatus.PROCESSING])
async def test_non_terminal_snapshot_commits_claim_before_http_and_schedules_next_query(
    state: WmsEffectStatus,
) -> None:
    _require_status_service()
    claim = _claim()
    db = _Db()
    repository = _Repository(claim)
    reducer = _Reducer()
    port = _Port(_snapshot(state), db)
    outbox_before = (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at)
    service = WmsEffectStatusService(
        repository=repository,
        reducer=reducer,
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: port,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == state.value
    assert db.commits == 2
    assert reducer.events[0].event_type.value == f"STATUS_{state.value}"
    assert claim.intent.status_source_version == 7
    assert claim.intent.status_check_after == NOW + timedelta(seconds=2)
    assert claim.intent.status_check_lease_token is None
    assert (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at) == outbox_before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [WmsEffectStatus.COMPLETED, WmsEffectStatus.REJECTED])
async def test_terminal_snapshot_clears_schedule_and_uses_unique_status_terminal_event(
    state: WmsEffectStatus,
) -> None:
    _require_status_service()
    claim = _claim()
    db = _Db()
    reducer = _Reducer()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(state), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == state.value
    assert reducer.events[0].event_type.value == f"STATUS_{state.value}"
    assert claim.intent.status_check_after is None
    assert claim.intent.status_source_version == 7


@pytest.mark.asyncio
async def test_completed_result_correlation_mismatch_fails_closed_before_terminal_reducer_event() -> None:
    _require_status_service()
    claim = _claim()
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    invalid = _snapshot(WmsEffectStatus.COMPLETED).model_copy(
        update={
            "result": NotifyPackageBindingOperationResult(
                dispatch_key="dispatch-001",
                package_id="OTHER-PACKAGE",
                pallet_id="PALLET-001",
                accepted=True,
                bound_at="2026-07-24T12:00:00Z",
                source_version="7",
            )
        }
    )
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(invalid, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RECONCILING"
    assert reducer.events == []
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_RESULT_IDENTITY_INVALID"


@pytest.mark.asyncio
async def test_late_worker_response_is_dropped_when_lease_token_no_longer_matches() -> None:
    _require_status_service()
    claim = _claim()
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim, fence_writeback=True),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.COMPLETED), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "FENCED"
    assert db.commits == 1
    assert reducer.events == []
    assert reconciliation.calls == []


@pytest.mark.asyncio
async def test_same_source_version_with_different_snapshot_opens_reconciliation_without_terminal_overwrite() -> None:
    _require_status_service()
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_source_version = 7
    claim.intent.outcome_history_json = [
        {
            "event_type": "STATUS_ACCEPTED",
            "source_version": 7,
            "snapshot_hash": "0" * 64,
        }
    ]
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.COMPLETED), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_SOURCE_VERSION_CONFLICT"
    assert reducer.events == []
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_stale_source_version_keeps_current_outcome_and_records_stale_evidence() -> None:
    _require_status_service()
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_source_version = 8
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: (
            lambda: _Port(_snapshot(WmsEffectStatus.PROCESSING, source_version=7), db)
        ),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "STALE"
    assert reducer.events[0].event_type.value == "STATUS_STALE"
    assert claim.intent.status_source_version == 8
    assert claim.intent.status_check_after == NOW + timedelta(seconds=2)
    assert reconciliation.calls == []


@pytest.mark.asyncio
async def test_lower_version_contradictory_terminal_is_reduced_for_reconciliation_instead_of_stale() -> None:
    _require_status_service()
    claim = _claim(status=RuntimeIntentStatus.COMPLETED)
    claim.intent.status_source_version = 8
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.REJECTED, source_version=7), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == WmsEffectStatus.REJECTED.value
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_REJECTED
    assert reducer.events[0].evidence_json["source_version"] == 7
    assert reconciliation.calls == []


@pytest.mark.asyncio
async def test_retry_after_is_a_lower_bound_and_query_failure_is_recorded_in_outcome_history() -> None:
    _require_status_service()
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    db = _Db()
    reducer = _Reducer()
    failure = SimpleNamespace(
        reason_code="WMS_RATE_LIMITED",
        retryable=True,
        retry_after_seconds=20.0,
    )
    error = RuntimeError("bounded query failure")
    error.failure = failure  # type: ignore[attr-defined]
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _FailingPort(error, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RETRY_SCHEDULED"
    assert claim.intent.status_check_after == NOW + timedelta(seconds=20)
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_QUERY_FAILED
    assert reducer.events[0].reason_code == "WMS_RATE_LIMITED"


@pytest.mark.asyncio
async def test_retry_after_above_local_max_remains_the_schedule_lower_bound() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    db = _Db()
    error = RuntimeError("provider rate limit")
    error.failure = SimpleNamespace(  # type: ignore[attr-defined]
        reason_code="WMS_RATE_LIMITED",
        retryable=True,
        retry_after_seconds=120.0,
    )
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _FailingPort(error, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RETRY_SCHEDULED"
    assert claim.intent.status_check_after == NOW + timedelta(seconds=120)


@pytest.mark.asyncio
async def test_non_retryable_query_contract_failure_opens_reconciliation_immediately() -> None:
    _require_status_service()
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    error = RuntimeError("frozen credential revision is unavailable")
    error.failure = SimpleNamespace(  # type: ignore[attr-defined]
        reason_code="WMS_CREDENTIAL_UNAVAILABLE",
    )
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _FailingPort(error, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RECONCILING"
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_QUERY_FAILED
    assert reconciliation.calls[0]["reason_code"] == "WMS_CREDENTIAL_UNAVAILABLE"
    assert claim.intent.status_check_after is None


@pytest.mark.asyncio
async def test_tampered_frozen_status_binding_fails_closed_to_reconciliation_before_http() -> None:
    _require_status_service()
    claim = _claim()
    claim.intent.status_binding_snapshot_json["auth_scheme"] = "NONE"
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    port_calls = 0

    def port_builder(_binding: Any) -> Any:
        nonlocal port_calls
        port_calls += 1
        return lambda: _Port(_snapshot(WmsEffectStatus.ACCEPTED), db)

    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=port_builder,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_BINDING_INVALID"
    assert port_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "snapshot_hash"),
    [
        (None, "a" * 64),
        ({}, None),
        ({"corrupt": True}, "b" * 64),
    ],
)
async def test_due_scanner_claims_missing_or_corrupt_binding_and_fails_closed_before_http(
    snapshot: object,
    snapshot_hash: str | None,
) -> None:
    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    claim.intent.status_binding_snapshot_json = snapshot
    claim.intent.status_binding_snapshot_hash = snapshot_hash
    claim.intent.operation_identity = NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY
    db = _Db()
    repository = _Repository(claim)
    reconciliation = _ReconciliationBridge()
    port_calls = 0

    def port_builder(_binding: Any) -> Any:
        nonlocal port_calls
        port_calls += 1
        return lambda: _Port(_snapshot(WmsEffectStatus.ACCEPTED), db)

    results = await WmsEffectStatusService(
        repository=repository,
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=port_builder,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_due_batch(db, limit=10)

    assert [result.outcome for result in results] == ["RECONCILING"]
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_BINDING_INVALID"
    assert repository.released == 1
    assert port_calls == 0


@pytest.mark.asyncio
async def test_corrupt_frozen_payload_is_normalized_to_request_contract_reconciliation() -> None:
    claim = _claim()
    claim.intent.operation_identity = NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY
    claim.outbox.payload_json.pop("package_id")
    db = _Db()
    reconciliation = _ReconciliationBridge()
    port_calls = 0

    def port_builder(_binding: Any) -> Any:
        nonlocal port_calls
        port_calls += 1
        return lambda: _Port(_snapshot(WmsEffectStatus.ACCEPTED), db)

    result = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        port_factory_builder=port_builder,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    ).check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_REQUEST_INVALID"
    assert claim.intent.status_check_lease_token is None
    assert port_calls == 0


def test_saturated_status_backoff_uses_injectable_random_source_without_collapsing_to_maximum() -> None:
    class RandomSource:
        def __init__(self, sample_ratio: float) -> None:
            self.sample_ratio = sample_ratio
            self.bounds: list[tuple[float, float]] = []

        def uniform(self, lower: float, upper: float) -> float:
            self.bounds.append((lower, upper))
            return lower + ((upper - lower) * self.sample_ratio)

    low_sample = RandomSource(0.0)
    high_sample = RandomSource(1.0)
    saturated = SimpleNamespace(status_check_count=1_000_000)
    low_delay = (
        WmsEffectStatusService(
            settings_source=_settings(),
            now=lambda: NOW,
            random_source=low_sample,
        )._next_check(saturated)
        - NOW
    ).total_seconds()
    high_delay = (
        WmsEffectStatusService(
            settings_source=_settings(),
            now=lambda: NOW,
            random_source=high_sample,
        )._next_check(saturated)
        - NOW
    ).total_seconds()

    assert low_sample.bounds == [(0.0, 15.0)]
    assert high_sample.bounds == [(0.0, 15.0)]
    assert {low_delay, high_delay} == {15.0, 30.0}


@pytest.mark.asyncio
async def test_not_found_after_grace_resubmits_same_key_once_after_counter_commit() -> None:
    _require_status_service()
    claim = _claim()
    claim.intent.status_check_started_at = NOW - timedelta(seconds=61)
    db = _Db()
    repository = _Repository(claim)
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    resubmit_calls: list[tuple[str, str, int]] = []

    async def resubmit(current_claim: Any) -> ExternalHttpTransportResult:
        assert db.commits == 2, "重提计数必须先提交，网络调用才能开始"
        resubmit_calls.append(
            (
                current_claim.outbox.operation_identity,
                current_claim.outbox.idempotency_key,
                current_claim.intent.status_resubmit_count,
            )
        )
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )

    not_found = WmsEffectStatusSnapshot.not_found(WmsEffectStatusService._build_request(claim))
    service = WmsEffectStatusService(
        repository=repository,
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(not_found, db),
        resubmit_dispatcher=resubmit,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    outbox_before = (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at)

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RESUBMITTED"
    assert repository.reserved == 1
    assert claim.intent.status_resubmit_count == 1
    assert resubmit_calls == [(NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY, "idem-001", 1)]
    assert (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at) == outbox_before
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_NOT_FOUND
    assert reconciliation.calls == []


def test_wms_effect_status_immediate_and_fallback_tasks_are_registered() -> None:
    from src.celery_app.config import beat_schedule
    from src.celery_app.tasks import workline

    assert "check_wms_effect_status" in workline.__all__
    assert "scan_wms_effect_status_batch" in workline.__all__
    assert beat_schedule["scan-wms-effect-status-batch"] == {
        "task": "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
        "schedule": 10.0,
    }


def test_wms_effect_status_repository_and_service_are_exported() -> None:
    from src.app.runtime.orchestration import repositories, services

    assert repositories.WmsEffectStatusRepository is not None
    assert repositories.wms_effect_status_repository is not None
    assert services.WmsEffectStatusService is WmsEffectStatusService
    assert services.wms_effect_status_service is not None


class _HintRepository(_Repository):
    def __init__(self, claim: Any, *, outcome: str = "SCHEDULED") -> None:
        super().__init__(claim)
        self.outcome = outcome
        self.hint_calls: list[dict[str, Any]] = []

    async def advance_status_check_after_from_hint(self, _db: Any, **kwargs: Any) -> str:
        self.hint_calls.append(kwargs)
        if self.outcome == "SCHEDULED":
            self.claim.intent.status_check_after = kwargs["now"]
        return self.outcome

    async def claim_due_batch(self, _db: Any, *, now: datetime, **_kwargs: Any) -> tuple[Any, ...]:
        if self.claim.intent.status_check_after is not None and self.claim.intent.status_check_after <= now:
            return (self.claim,)
        return ()


class _HintQueue:
    def __init__(self, db: _Db, *, error: Exception | None = None) -> None:
        self.db = db
        self.error = error
        self.dispatch_keys: list[str] = []

    def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None:
        assert self.db.commits == 1, "hint 到期时间必须先提交，才能触发即时状态查询"
        self.dispatch_keys.append(dispatch_key)
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_identity", sorted(WMS_EFFECT_OPERATION_IDENTITIES))
async def test_status_hint_persists_due_time_and_commits_before_immediate_enqueue(operation_identity: str) -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.operation_identity = "BUSINESS:PKG-001:PALLET-001"
    claim.outbox.operation_identity = operation_identity
    claim.intent.status_check_after = NOW + timedelta(minutes=5)
    db = _Db()
    repository = _HintRepository(claim)
    queue = _HintQueue(db)
    outbox_before = (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at)
    service = WmsEffectStatusService(
        repository=repository,
        queue_gateway=queue,
        settings_source=_settings(),
        now=lambda: NOW,
    )

    result = await service.request_status_check_hint(
        db,
        operation_identity=claim.outbox.operation_identity,
        idempotency_key=claim.outbox.idempotency_key,
        dispatch_key=claim.outbox.dispatch_key,
    )

    assert result.outcome == "SCHEDULED"
    assert claim.intent.operation_identity != operation_identity
    assert claim.intent.status_check_after == NOW
    assert db.commits == 1
    assert queue.dispatch_keys == ["dispatch-001"]
    assert repository.hint_calls == [
        {
            "operation_identity": claim.outbox.operation_identity,
            "idempotency_key": claim.outbox.idempotency_key,
            "dispatch_key": claim.outbox.dispatch_key,
            "now": NOW,
        }
    ]
    assert (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at) == outbox_before


@pytest.mark.asyncio
async def test_broker_failure_after_hint_commit_returns_success_and_leaves_due_row_for_beat() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    claim.intent.status_check_after = NOW + timedelta(minutes=5)
    db = _Db()
    repository = _HintRepository(claim)
    queue = _HintQueue(db, error=RuntimeError("broker secret-token"))
    intent_before = (claim.intent.effect_status, claim.intent.outcome_kind, dict(claim.intent.outcome_json))
    outbox_before = (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at)
    service = WmsEffectStatusService(
        repository=repository,
        queue_gateway=queue,
        settings_source=_settings(),
        now=lambda: NOW,
    )

    with patch("src.app.runtime.orchestration.services.wms_effect_status_service.logger.warning") as warning:
        result = await service.request_status_check_hint(
            db,
            operation_identity=claim.outbox.operation_identity,
            idempotency_key=claim.outbox.idempotency_key,
            dispatch_key=claim.outbox.dispatch_key,
        )
    due_claims = await repository.claim_due_batch(db, now=NOW, lease_seconds=10, limit=10)

    assert result.outcome == "SCHEDULED"
    assert due_claims == (claim,)
    assert db.commits == 1
    assert (claim.intent.effect_status, claim.intent.outcome_kind, claim.intent.outcome_json) == intent_before
    assert (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at) == outbox_before
    warning.assert_called_once()
    warning_text = str(warning.call_args.args[0])
    assert "wms_effect_status_hint_enqueue_failed_total" in warning_text
    assert sha256(b"dispatch-001").hexdigest()[:16] in warning_text
    assert "dispatch-001" not in warning_text
    assert "secret-token" not in warning_text


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ALREADY_DUE", "TERMINAL"])
async def test_duplicate_or_terminal_hint_is_ignored_without_enqueue(outcome: str) -> None:
    claim = _claim(status=RuntimeIntentStatus.COMPLETED if outcome == "TERMINAL" else RuntimeIntentStatus.ACCEPTED)
    db = _Db()
    repository = _HintRepository(claim, outcome=outcome)
    queue = _HintQueue(db)
    service = WmsEffectStatusService(
        repository=repository,
        queue_gateway=queue,
        settings_source=_settings(),
        now=lambda: NOW,
    )

    result = await service.request_status_check_hint(
        db,
        operation_identity=claim.outbox.operation_identity,
        idempotency_key=claim.outbox.idempotency_key,
        dispatch_key=claim.outbox.dispatch_key,
    )

    assert result.outcome == outcome
    assert db.commits == 0
    assert queue.dispatch_keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["NOT_FOUND", "CORRELATION_MISMATCH"])
async def test_unknown_or_mismatched_hint_is_named_failure(outcome: str) -> None:
    claim = _claim()
    service = WmsEffectStatusService(
        repository=_HintRepository(claim, outcome=outcome),
        queue_gateway=_HintQueue(_Db()),
        settings_source=_settings(),
        now=lambda: NOW,
    )

    with pytest.raises(ValueError, match=f"WMS_EFFECT_STATUS_HINT_{outcome}"):
        await service.request_status_check_hint(
            _Db(),
            operation_identity=claim.outbox.operation_identity,
            idempotency_key=claim.outbox.idempotency_key,
            dispatch_key=claim.outbox.dispatch_key,
        )
