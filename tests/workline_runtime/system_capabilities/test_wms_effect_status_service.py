"""WMS EFFECT 状态查询 orchestration、lease fencing 与版本归并合同。"""

from __future__ import annotations

import inspect
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.effect_status import (
    WMS_EFFECT_OPERATION_IDENTITIES,
    WmsBatchEffectStatusRequest,
    WmsEffectStatus,
    WmsEffectStatusRequest,
    WmsEffectStatusSnapshot,
    build_wms_effect_status_binding,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.ports.fulfillment_operations import RequestRackSupplyResult, WmsEffectAck
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
)
from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

try:
    from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusClaim
    from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
except ImportError as exc:
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None


NOW = datetime(2026, 7, 24, 12, 0, 0)
RACK_SUPPLY_OPERATION_IDENTITY = "wms.fulfillment.request_rack_supply@v1"
_STATUS_PROFILE_PAYLOAD = build_hmac_provider_profile_payload()
_STATUS_PROFILE_PAYLOAD["server_url"] = "https://wms.example"
_STATUS_PROFILE_PAYLOAD["effect_status_path"] = "/northbound/operations/status"
_STATUS_COMPILED_PROFILE = build_compiled_provider_profile(_STATUS_PROFILE_PAYLOAD)


def _require_status_service() -> None:
    assert _IMPORT_ERROR is None, f"WMS EFFECT status service 尚未实现: {_IMPORT_ERROR}"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        APP_ENV="test",
        WMS_EFFECT_STATUS_TIMEOUT_SECONDS=2.0,
        WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES=4096,
        WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS=10.0,
        WES_EFFECT_STATUS_SCAN_BATCH_SIZE=3,
        WES_EFFECT_STATUS_MAX_IN_FLIGHT=2,
        WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS=2.0,
        WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS=30.0,
        WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS=3,
        WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=300.0,
        WES_EFFECT_NOT_FOUND_GRACE_SECONDS=60.0,
    )


@asynccontextmanager
async def _open_db(db: Any):
    yield db


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
    binding = build_wms_effect_status_binding(
        settings_source=_settings(),
        compiled_profile=_STATUS_COMPILED_PROFILE,
    ).as_persisted()
    request_payload = {
        "dispatch_key": "dispatch-001",
        "station_code": "STATION-001",
        "rack_type": "FLOW_RACK",
        "demand_generation": 1,
    }
    canonical_payload = CanonicalPayload.from_projection(request_payload)
    payload_hash = canonical_payload.sha256
    frozen_ack = {
        "operation_identity": RACK_SUPPLY_OPERATION_IDENTITY,
        "idempotency_key": "idem-001",
        "provider_reference": "wms-effect-001",
        "submission_state": "ACCEPTED",
        "accepted_scope": None,
    }
    ack_hash = typed_wms_effect_ack_hash(WmsEffectAck.model_validate(frozen_ack))
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
        outcome_history_json=[
            {
                "event_type": EffectReducerEventType.TRANSPORT_ACCEPTED.value,
                "typed_ack_hash": ack_hash,
                "typed_ack_reference": "runtime-intent-outcome:dispatch-001",
            }
        ],
        outcome_kind=None,
        outcome_code=None,
        outcome_json={
            "payload_hash": payload_hash,
            "outcome": {"kind": "success", "payload": frozen_ack},
        },
        capability_key="wms.fulfillment.request_rack_supply",
        capability_contract_version="v1",
        operation_identity="WMS:PKG-001",
        payload_hash=payload_hash,
        effect_updated_at_ms=None,
    )
    outbox = SimpleNamespace(
        id=31,
        dispatch_key="dispatch-001",
        idempotency_key="idem-001",
        operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
        provider_profile_identity=binding["snapshot"]["provider_profile_identity"],
        provider_profile_hash=binding["snapshot"]["provider_profile_hash"],
        payload_json=request_payload,
        payload_hash=payload_hash,
        canonical_payload_bytes=canonical_payload.body,
        status="SENT",
        attempt_count=4,
        next_retry_at=NOW + timedelta(minutes=5),
    )
    return WmsEffectStatusClaim(intent=intent, outbox=outbox, lease_token="lease-001")


def test_status_request_recovers_the_frozen_ack_from_persisted_transport_evidence() -> None:
    request = WmsEffectStatusService._build_request(_claim())

    assert request.frozen_ack.provider_reference == "wms-effect-001"
    assert request.frozen_ack.operation_identity == request.operation_identity
    assert request.frozen_ack.idempotency_key == request.idempotency_key


def test_status_request_allows_status_first_when_persisted_ack_does_not_exist() -> None:
    claim = _claim()
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None

    request = WmsEffectStatusService._build_request(claim)

    assert request.frozen_ack is None


def test_status_request_rejects_authoritative_ack_without_append_only_evidence() -> None:
    claim = _claim()
    claim.intent.outcome_history_json = []

    with pytest.raises(ValueError, match="ACK evidence is missing"):
        WmsEffectStatusService._build_request(claim)


def test_status_request_rejects_invalid_authoritative_ack_without_append_only_evidence() -> None:
    claim = _claim()
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json["outcome"]["payload"] = {}

    with pytest.raises(ValueError, match="ACK evidence is invalid"):
        WmsEffectStatusService._build_request(claim)


def test_status_request_does_not_treat_non_success_outcome_as_an_ack() -> None:
    claim = _claim()
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = {"outcome": {"kind": "contract_violation", "payload": {}}}

    request = WmsEffectStatusService._build_request(claim)

    assert request.frozen_ack is None


def test_status_request_rejects_transport_acceptance_without_typed_ack_hash() -> None:
    claim = _claim()
    claim.intent.outcome_history_json[0].pop("typed_ack_hash")

    with pytest.raises(ValueError, match="ACK evidence is invalid"):
        WmsEffectStatusService._build_request(claim)


@pytest.mark.parametrize(
    ("drift", "expected"),
    (
        ("operation_identity", "capability identity"),
        ("intent_payload_hash", "payload fingerprint"),
        ("canonical_hash", "canonical_payload_bytes"),
        ("canonical_projection", "canonical payload projection"),
    ),
)
def test_status_request_rejects_any_frozen_intent_outbox_pair_drift(drift: str, expected: str) -> None:
    claim = _claim()
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None
    if drift == "operation_identity":
        claim.intent.capability_contract_version = "v2"
    elif drift == "intent_payload_hash":
        claim.intent.payload_hash = "f" * 64
    elif drift == "canonical_hash":
        claim.outbox.canonical_payload_bytes = b'{"forged":true}'
    else:
        claim.outbox.payload_json = {**claim.outbox.payload_json, "station_code": "FORGED"}

    with pytest.raises(ValueError, match=expected):
        WmsEffectStatusService._build_request(claim)


def test_status_request_rejects_provider_reference_drift_across_persisted_ack_evidence() -> None:
    claim = _claim()
    drifted_ack = {
        **claim.intent.outcome_json["outcome"]["payload"],
        "provider_reference": "other-provider-reference",
        "submission_state": "REPLAY",
    }
    claim.intent.outcome_history_json.append(
        {
            "event_type": EffectReducerEventType.TRANSPORT_ACCEPTED.value,
            "typed_ack_hash": typed_wms_effect_ack_hash(WmsEffectAck.model_validate(drifted_ack)),
            "typed_ack_reference": "runtime-intent-outcome:dispatch-001",
        }
    )

    with pytest.raises(ValueError, match="ACK evidence drifted"):
        WmsEffectStatusService._build_request(claim)


@pytest.mark.asyncio
async def test_status_first_visible_snapshot_freezes_ack_before_status_evidence() -> None:
    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None
    db = _Db()
    reducer = _Reducer()
    request = WmsEffectStatusService._build_request(claim)
    snapshot = parse_wms_effect_status_snapshot(
        request=request,
        raw_response={
            "state": "PROCESSING",
            "provider_reference": "provider-status-first",
            "accepted_scope": None,
            "reason_code": None,
            "updated_at": "2026-07-24T12:00:00+00:00",
            "source_version": 1,
            "result_payload": None,
        },
    )
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "PROCESSING"
    assert [event.event_type for event in reducer.events] == [
        EffectReducerEventType.TRANSPORT_ACCEPTED,
        EffectReducerEventType.STATUS_PROCESSING,
    ]
    recovered = reducer.events[0]
    assert recovered.terminal_outcome["payload"]["provider_reference"] == "provider-status-first"
    assert recovered.evidence_json.keys() >= {"typed_ack_hash", "typed_ack_reference"}
    assert "payload" not in recovered.evidence_json
    assert "recovered_ack" not in reducer.events[1].evidence_json["snapshot"]


def test_visible_snapshot_without_frozen_or_recovered_ack_fails_closed() -> None:
    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None

    with pytest.raises(ValueError, match="frozen or recovered ACK"):
        WmsEffectStatusService._validate_snapshot_matches_claim(
            claim,
            WmsEffectStatusSnapshot(
                operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
                idempotency_key="idem-001",
                state=WmsEffectStatus.PROCESSING,
                provider_reference="provider-unfrozen",
                updated_at=NOW.replace(tzinfo=UTC),
                source_version=1,
            ),
        )


def test_visible_snapshot_rejects_recovered_ack_drift_from_existing_authority() -> None:
    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    recovered_ack = WmsEffectAck(
        operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
        idempotency_key="idem-001",
        provider_reference="provider-drift",
        submission_state="REPLAY",
    )

    with pytest.raises(ValueError, match="recovered WMS status ACK differs"):
        WmsEffectStatusService._validate_snapshot_matches_claim(
            claim,
            WmsEffectStatusSnapshot(
                operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
                idempotency_key="idem-001",
                state=WmsEffectStatus.PROCESSING,
                provider_reference=recovered_ack.provider_reference,
                updated_at=NOW.replace(tzinfo=UTC),
                source_version=1,
                recovered_ack=recovered_ack,
            ),
        )


@pytest.mark.parametrize("state", [WmsEffectStatus.PROCESSING, WmsEffectStatus.COMPLETED])
def test_batch_snapshot_validation_reuses_frozen_scope_for_nonterminal_and_terminal(state: WmsEffectStatus) -> None:
    operation_identity = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
    request_payload = REQUEST_FIXTURES[operation_identity]
    ack = WmsEffectAck.model_validate(
        build_typed_ack(operation_identity, "idem-batch", request_payload, submission_state="ACCEPTED")
    )
    request = WmsBatchEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key="idem-batch",
        request_payload=request_payload,
        frozen_ack=ack,
    )
    snapshot_kwargs: dict[str, Any] = {}
    if state is WmsEffectStatus.COMPLETED:
        snapshot_kwargs["result"] = WMS_OPERATION_BY_IDENTITY[operation_identity].result_model.model_validate(
            build_typed_result(
                operation_identity,
                request_payload,
                source_version=3,
                completed_at="2026-07-24T12:00:00+00:00",
                provider_reference=ack.provider_reference,
            )
        )
    snapshot = WmsEffectStatusSnapshot(
        operation_identity=operation_identity,
        idempotency_key="idem-batch",
        state=state,
        provider_reference=ack.provider_reference,
        updated_at=NOW.replace(tzinfo=UTC),
        source_version=3,
        **snapshot_kwargs,
    )

    with patch.object(WmsEffectStatusService, "_build_request", return_value=request):
        validated = WmsEffectStatusService._validate_snapshot_matches_claim(_claim(), snapshot)

    assert validated is request


@pytest.mark.parametrize("cross_wire", ("idempotency-key", "payload-fingerprint", "provider-reference"))
def test_status_request_rejects_frozen_ack_cross_wire(cross_wire: str) -> None:
    claim = _claim()
    if cross_wire == "idempotency-key":
        claim.intent.outcome_json["outcome"]["payload"]["idempotency_key"] = "other-key"
    elif cross_wire == "payload-fingerprint":
        claim.intent.outcome_json["payload_hash"] = "0" * 64
    else:
        drifted_ack = {
            **claim.intent.outcome_json["outcome"]["payload"],
            "provider_reference": "other-provider-reference",
            "submission_state": "REPLAY",
        }
        claim.intent.outcome_history_json.append(
            {
                "event_type": EffectReducerEventType.TRANSPORT_ACCEPTED.value,
                "typed_ack_hash": typed_wms_effect_ack_hash(WmsEffectAck.model_validate(drifted_ack)),
                "typed_ack_reference": "runtime-intent-outcome:dispatch-001",
            }
        )

    with pytest.raises(ValueError, match=r"ACK|fingerprint|payload_hash"):
        WmsEffectStatusService._build_request(claim)


@pytest.mark.parametrize(
    "authoritative",
    (
        None,
        {"outcome": {"kind": "contract_violation", "payload": {}}},
        {"outcome": {"kind": "success", "payload": {}}},
    ),
)
def test_status_request_rejects_invalid_authoritative_ack_envelope(authoritative: object) -> None:
    claim = _claim()
    claim.intent.outcome_json = authoritative

    with pytest.raises(ValueError, match="ACK evidence is invalid"):
        WmsEffectStatusService._build_request(claim)


@pytest.mark.asyncio
async def test_original_key_resubmit_rejects_an_invalid_typed_ack() -> None:
    claim = _claim()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    result = ExternalHttpTransportResult.accepted(
        http_status_code=200,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        response_body=b"{}",
    )

    recorded = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=result,
        evidence={"recovery": "original-key"},
    )

    assert recorded.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_RESUBMIT_INDETERMINATE"


@pytest.mark.asyncio
async def test_original_key_resubmit_rejects_idempotency_conflict() -> None:
    claim = _claim()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    result = ExternalHttpTransportResult.accepted(
        http_status_code=422,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        protocol_error_code="IDEMPOTENCY_CONFLICT",
        response_body=b'{"protocol_error_code":"IDEMPOTENCY_CONFLICT"}',
    )

    recorded = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=result,
        evidence={"recovery": "original-key"},
    )

    assert recorded.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_RESUBMIT_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_original_key_resubmit_rejects_frozen_pair_drift_before_ack_interpretation() -> None:
    claim = _claim()
    claim.outbox.canonical_payload_bytes = b'{"forged":true}'
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    result = ExternalHttpTransportResult.accepted(
        http_status_code=200,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        response_body=json.dumps(
            claim.intent.outcome_json["outcome"]["payload"],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(),
    )

    recorded = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=result,
        evidence={"recovery": "original-key"},
    )

    assert recorded.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_RESUBMIT_INDETERMINATE"


@pytest.mark.asyncio
async def test_original_key_resubmit_freezes_first_authoritative_ack() -> None:
    claim = _claim()
    replay_ack = {
        **claim.intent.outcome_json["outcome"]["payload"],
        "submission_state": "REPLAY",
    }
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None
    reconciliation = _ReconciliationBridge()
    reducer = _Reducer()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    result = ExternalHttpTransportResult.accepted(
        http_status_code=200,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        response_body=json.dumps(replay_ack, ensure_ascii=False, separators=(",", ":")).encode(),
    )

    recorded = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=result,
        evidence={"recovery": "original-key"},
    )

    assert recorded.outcome == "RESUBMITTED"
    assert reconciliation.calls == []
    assert len(reducer.events) == 1
    recovered = reducer.events[0]
    assert recovered.event_type is EffectReducerEventType.TRANSPORT_ACCEPTED
    assert recovered.evidence_json.keys() >= {"typed_ack_hash", "typed_ack_reference"}
    assert "payload" not in recovered.evidence_json
    assert recovered.terminal_outcome == {"kind": "success", "payload": replay_ack}


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

    async def get_due_backlog_snapshot(self, _db: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            backlog_count=0 if self.batch_claimed else 1,
            max_overdue_age_ms=0.0,
            max_confirmation_age_ms=0.0,
        )

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
            operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
            idempotency_key="idem-001",
            state=state,
            result=RequestRackSupplyResult(
                dispatch_key="dispatch-001",
                provider_reference="wms-effect-001",
                source_version=str(source_version),
                station_code="STATION-001",
                rack_type="FLOW_RACK",
                demand_generation=1,
                rack_id="RACK-001",
                final_station_code="STATION-001",
                arrival_relation="AT_STATION",
                task_outcome="SUCCESS",
            ),
            **visible,
        )
    if state is WmsEffectStatus.REJECTED:
        return WmsEffectStatusSnapshot(
            operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
            idempotency_key="idem-001",
            state=state,
            reason_code="NO_RACK_AVAILABLE",
            **visible,
        )
    return WmsEffectStatusSnapshot(
        operation_identity=RACK_SUPPLY_OPERATION_IDENTITY,
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
@pytest.mark.parametrize("state", [WmsEffectStatus.ACCEPTED, WmsEffectStatus.PROCESSING])
async def test_terminal_intent_rejects_non_terminal_regression_without_rescheduling(
    state: WmsEffectStatus,
) -> None:
    claim = _claim(status=RuntimeIntentStatus.COMPLETED)
    claim.intent.status_source_version = 6
    db = _Db()
    repository = _Repository(claim)
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=repository,
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(state, source_version=7), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_TERMINAL_REGRESSION"
    assert reducer.events == []
    assert claim.intent.status_check_after is None
    assert repository.released == 1


@pytest.mark.asyncio
async def test_completed_result_correlation_mismatch_fails_closed_before_terminal_reducer_event() -> None:
    _require_status_service()
    claim = _claim()
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    invalid = _snapshot(WmsEffectStatus.COMPLETED).model_copy(
        update={
            "result": RequestRackSupplyResult(
                dispatch_key="dispatch-001",
                provider_reference="wms-effect-001",
                source_version="7",
                station_code="OTHER-STATION",
                rack_type="FLOW_RACK",
                demand_generation=1,
                rack_id="RACK-001",
                final_station_code="STATION-001",
                arrival_relation="AT_STATION",
                task_outcome="SUCCESS",
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
        *claim.intent.outcome_history_json,
        {
            "event_type": "STATUS_ACCEPTED",
            "source_version": 7,
            "snapshot_hash": "0" * 64,
        },
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
async def test_retry_after_is_a_lower_bound_and_query_failure_is_recorded_in_outcome_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration import wms_effect_observability

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
    emissions: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        wms_effect_observability,
        "emit_wms_effect_observation",
        lambda name, **kwargs: emissions.append((name, kwargs)),
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RETRY_SCHEDULED"
    assert claim.intent.status_check_after == NOW + timedelta(seconds=20)
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_QUERY_FAILED
    assert reducer.events[0].reason_code == "WMS_RATE_LIMITED"
    assert emissions[0][0] == "wms_effect.status_backpressure"
    assert emissions[0][1]["attributes"] == {
        "outcome": "RATE_LIMITED",
        "retry_after_ms": 20_000,
        "actual_backoff_ms": 20_000,
    }


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
async def test_non_retryable_query_contract_failure_opens_reconciliation_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration import wms_effect_observability

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
    emissions: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        wms_effect_observability,
        "emit_wms_effect_observation",
        lambda name, **kwargs: emissions.append((name, kwargs)),
    )

    result = await service.check_dispatch(db, dispatch_key="dispatch-001")

    assert result.outcome == "RECONCILING"
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_QUERY_FAILED
    assert reconciliation.calls[0]["reason_code"] == "WMS_CREDENTIAL_UNAVAILABLE"
    assert claim.intent.status_check_after is None
    assert emissions[0][0] == "wms_effect.recovery"
    assert emissions[0][1]["attributes"]["outcome"] == "RECONCILIATION_OPENED"


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
    claim.intent.operation_identity = RACK_SUPPLY_OPERATION_IDENTITY
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
        db_context_factory=lambda: _open_db(db),
    ).check_due_batch(db)

    assert [result.outcome for result in results] == ["RECONCILING"]
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_BINDING_INVALID"
    assert repository.released == 1
    assert port_calls == 0


@pytest.mark.asyncio
async def test_due_scanner_emits_status_query_and_batch_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.runtime.orchestration import wms_effect_observability

    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    db = _Db()
    emissions: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        wms_effect_observability,
        "emit_wms_effect_observation",
        lambda name, **kwargs: emissions.append((name, kwargs)),
    )

    results = await WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.ACCEPTED), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
        db_context_factory=lambda: _open_db(db),
    ).check_due_batch(db)

    assert [result.outcome for result in results] == ["ACCEPTED"]
    assert [name for name, _kwargs in emissions] == ["wms_effect.status_query", "wms_effect.status_backlog"]
    assert emissions[0][1]["attributes"]["state"] == "ACCEPTED"
    assert emissions[1][1]["attributes"]["backlog_count"] == 1
    assert emissions[1][1]["attributes"]["claimed_count"] == 1


@pytest.mark.asyncio
async def test_corrupt_frozen_payload_is_normalized_to_request_contract_reconciliation() -> None:
    claim = _claim()
    claim.intent.operation_identity = RACK_SUPPLY_OPERATION_IDENTITY
    claim.outbox.payload_json.pop("station_code")
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
        replay_ack = {
            **current_claim.intent.outcome_json["outcome"]["payload"],
            "submission_state": "ACCEPTED",
        }
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            response_body=json.dumps(replay_ack, ensure_ascii=False, separators=(",", ":")).encode(),
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
    assert resubmit_calls == [(RACK_SUPPLY_OPERATION_IDENTITY, "idem-001", 1)]
    assert (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at) == outbox_before
    assert reducer.events[0].event_type is EffectReducerEventType.STATUS_NOT_FOUND
    assert reconciliation.calls == []


def test_wms_effect_status_immediate_and_fallback_tasks_are_registered() -> None:
    from src.celery_app.config import beat_schedule
    from src.celery_app.tasks import workline

    assert "check_wms_effect_status" in workline.__all__
    assert "scan_wms_effect_status_batch" in workline.__all__
    assert tuple(inspect.signature(workline.scan_wms_effect_status_batch).parameters) == ()
    assert tuple(inspect.signature(WmsEffectStatusService.check_due_batch).parameters) == ("self", "db")
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
async def test_broker_failure_after_hint_commit_returns_success_and_leaves_due_row_for_beat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration import wms_effect_observability

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
    emissions: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        wms_effect_observability,
        "emit_wms_effect_observation",
        lambda name, **kwargs: emissions.append((name, kwargs)),
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
    assert emissions == [
        (
            "wms_effect.callback_hint",
            {
                "operation_identity": claim.outbox.operation_identity,
                "dispatch_key": "dispatch-001",
                "attributes": {"outcome": "ENQUEUE_DEGRADED"},
            },
        )
    ]


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
