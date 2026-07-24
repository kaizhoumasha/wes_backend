"""WMS EFFECT 状态查询的 typed Port 与 adapter 合同。"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from src.app.wms_integration import runtime_factory
from src.app.wms_integration.adapters import effect_status_query_adapter
from src.app.wms_integration.adapters.effect_status_query_adapter import (
    WmsEffectStatusQueryAdapter,
    WmsEffectStatusQueryError,
)
from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationResult
from src.app.wms_integration.ports.effect_status import (
    ConfirmInboundResultIdentity,
    FrozenWmsEffectStatusBinding,
    FullBoxExchangeResultIdentity,
    NotifyPackageBindingResultIdentity,
    WmsEffectStatus,
    WmsEffectStatusRequest,
    WmsEffectStatusSnapshot,
    assert_status_snapshot_progression,
    build_wms_effect_status_binding,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationResult
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationResult
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QuerySuccess, QueryTechnicalFailure
from src.app.wms_integration.runtime_factory import build_effect_status_query_port_factory
from src.app.wms_integration.services.query_transport import WmsQueryCallPermit

CONFIRM_INBOUND = "wms.inventory.confirm_inbound@v1"
FULL_BOX_EXCHANGE = "wms.fulfillment.full_box_exchange@v1"
NOTIFY_PACKAGE_BINDING = "wms.fulfillment.notify_pkg_binding@v1"


def _binding(*, timeout_seconds: float = 2.0) -> FrozenWmsEffectStatusBinding:
    settings = SimpleNamespace(
        APP_ENV="test",
        WMS_EFFECT_STATUS_URL="https://wms.example/northbound/operations/status",
        WMS_EFFECT_STATUS_TIMEOUT_SECONDS=timeout_seconds,
        WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES=4096,
    )
    return build_wms_effect_status_binding(settings_source=settings)


def _notify_request(*, attempt_count: int = 1) -> WmsEffectStatusRequest:
    return WmsEffectStatusRequest(
        operation_identity=NOTIFY_PACKAGE_BINDING,
        idempotency_key="intent-idempotency-001",
        attempt_count=attempt_count,
        expected_result_identity=NotifyPackageBindingResultIdentity(
            dispatch_key="dispatch-001",
            package_id="PKG-001",
            pallet_id="PALLET-001",
        ),
    )


def _completed_wire(*, source_version: int = 3) -> dict[str, object]:
    return {
        "state": "COMPLETED",
        "provider_reference": "wms-effect-001",
        "reason_code": None,
        "updated_at": "2026-07-24T00:00:00+00:00",
        "source_version": source_version,
        "result_payload": {
            "dispatch_key": "dispatch-001",
            "package_id": "PKG-001",
            "pallet_id": "PALLET-001",
            "accepted": True,
            "bound_at": "2026-07-24T00:00:00Z",
            "reason_code": None,
            "source_version": str(source_version),
        },
    }


def test_status_request_accepts_only_authored_effect_identity_and_stable_key() -> None:
    request = _notify_request()

    assert request.query_params == {
        "operation_identity": NOTIFY_PACKAGE_BINDING,
        "idempotency_key": "intent-idempotency-001",
    }
    assert "dispatch_key" not in request.query_params
    opaque_key = WmsEffectStatusRequest(
        operation_identity=request.operation_identity,
        idempotency_key=" opaque-key ",
        expected_result_identity=request.expected_result_identity,
    )
    assert opaque_key.idempotency_key == " opaque-key "
    assert opaque_key.query_params["idempotency_key"] == " opaque-key "
    assert WmsEffectStatusSnapshot.not_found(opaque_key).idempotency_key == " opaque-key "

    with pytest.raises(ValidationError, match="operation_identity"):
        WmsEffectStatusRequest(
            operation_identity="wms.unknown.operation@v1",
            idempotency_key="intent-idempotency-001",
            expected_result_identity=request.expected_result_identity,
        )
    with pytest.raises(ValidationError, match="idempotency_key"):
        WmsEffectStatusRequest(
            operation_identity=NOTIFY_PACKAGE_BINDING,
            idempotency_key=" ",
            expected_result_identity=request.expected_result_identity,
        )


def test_status_binding_is_hashed_immutable_non_secret_and_strictly_round_trips() -> None:
    binding = _binding()
    persisted = binding.as_persisted()

    assert persisted["snapshot"]["target"]["url"] == "https://wms.example/northbound/operations/status"
    assert persisted["snapshot"]["credential_reference"].endswith("@v1")
    assert "secret" not in repr(persisted).lower().replace("secret://", "")
    assert FrozenWmsEffectStatusBinding.from_persisted(**persisted) == binding
    with pytest.raises(ValidationError, match="frozen"):
        binding.auth_scheme = "NONE"  # type: ignore[misc]

    tampered = {**persisted, "snapshot": {**persisted["snapshot"], "auth_scheme": "NONE"}}
    with pytest.raises(ValueError, match=r"hash|auth"):
        FrozenWmsEffectStatusBinding.from_persisted(**tampered)


@pytest.mark.parametrize("state", ("ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"))
def test_snapshot_state_is_a_closed_five_value_set(state: str) -> None:
    request = _notify_request()
    if state == "COMPLETED":
        wire = _completed_wire()
    elif state == "REJECTED":
        wire = {
            "state": state,
            "provider_reference": "wms-effect-001",
            "reason_code": "WMS_BUSINESS_REJECTED",
            "updated_at": "2026-07-24T00:00:00+00:00",
            "source_version": 3,
            "result_payload": None,
        }
    elif state == "NOT_FOUND":
        wire = {
            "state": state,
            "provider_reference": None,
            "reason_code": None,
            "updated_at": None,
            "source_version": None,
            "result_payload": None,
        }
    else:
        wire = {
            "state": state,
            "provider_reference": "wms-effect-001",
            "reason_code": None,
            "updated_at": "2026-07-24T00:00:00+00:00",
            "source_version": 3,
            "result_payload": None,
        }

    snapshot = parse_wms_effect_status_snapshot(request=request, raw_response=wire)

    assert snapshot.state is WmsEffectStatus(state)
    assert snapshot.operation_identity == request.operation_identity
    assert snapshot.idempotency_key == request.idempotency_key


def test_completed_snapshot_returns_only_operation_specific_typed_result() -> None:
    request = _notify_request()

    snapshot = parse_wms_effect_status_snapshot(request=request, raw_response=_completed_wire())

    assert isinstance(snapshot.result, NotifyPackageBindingOperationResult)
    assert snapshot.result.package_id == "PKG-001"
    assert not hasattr(snapshot, "raw_payload")


@pytest.mark.parametrize(
    "changes",
    [
        {"state": WmsEffectStatus.COMPLETED},
        {
            "state": WmsEffectStatus.NOT_FOUND,
            "provider_reference": "wms-effect-001",
        },
        {
            "state": WmsEffectStatus.ACCEPTED,
            "provider_reference": "wms-effect-001",
            "updated_at": datetime(2026, 7, 24, tzinfo=UTC),
            "source_version": 3,
            "reason_code": "WMS_BUSINESS_REJECTED",
        },
        {
            "state": WmsEffectStatus.REJECTED,
            "provider_reference": "wms-effect-001",
            "updated_at": datetime(2026, 7, 24, tzinfo=UTC),
            "source_version": 3,
            "reason_code": "PALLET_LOCKED",
        },
    ],
)
def test_snapshot_direct_construction_enforces_domain_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match=r"visible|NOT_FOUND|reason|result"):
        WmsEffectStatusSnapshot(
            operation_identity=NOTIFY_PACKAGE_BINDING,
            idempotency_key="intent-idempotency-001",
            **changes,
        )


def test_snapshot_direct_construction_rejects_result_for_wrong_state_or_operation() -> None:
    completed = parse_wms_effect_status_snapshot(request=_notify_request(), raw_response=_completed_wire())
    visible_fields = {
        "provider_reference": "wms-effect-001",
        "updated_at": datetime(2026, 7, 24, tzinfo=UTC),
        "source_version": 3,
    }

    with pytest.raises(ValidationError, match="COMPLETED"):
        WmsEffectStatusSnapshot(
            operation_identity=NOTIFY_PACKAGE_BINDING,
            idempotency_key="intent-idempotency-001",
            state=WmsEffectStatus.PROCESSING,
            result=completed.result,
            **visible_fields,
        )
    with pytest.raises(ValidationError, match="operation"):
        WmsEffectStatusSnapshot(
            operation_identity=CONFIRM_INBOUND,
            idempotency_key="intent-idempotency-001",
            state=WmsEffectStatus.COMPLETED,
            result=completed.result,
            **visible_fields,
        )


@pytest.mark.parametrize(
    ("operation_identity", "identity", "payload", "result_type"),
    [
        (
            CONFIRM_INBOUND,
            ConfirmInboundResultIdentity(dispatch_key="dispatch-confirm", inbound_key="IN-001"),
            {
                "dispatch_key": "dispatch-confirm",
                "inbound_key": "IN-001",
                "accepted": True,
                "document_no": "GRN-001",
                "reason_code": None,
                "source_version": "7",
            },
            ConfirmInboundOperationResult,
        ),
        (
            FULL_BOX_EXCHANGE,
            FullBoxExchangeResultIdentity(
                dispatch_key="dispatch-box",
                rack_id="RACK-001",
                empty_box_id="EMPTY-001",
                full_box_id="FULL-001",
            ),
            {
                "dispatch_key": "dispatch-box",
                "rack_id": "RACK-001",
                "empty_box_id": "EMPTY-001",
                "full_box_id": "FULL-001",
                "accepted": True,
                "exchange_request_code": "EX-001",
                "reason_code": None,
                "source_version": "7",
            },
            FullBoxExchangeOperationResult,
        ),
    ],
)
def test_completed_snapshot_selects_result_model_from_frozen_registry(
    operation_identity: str,
    identity: object,
    payload: dict[str, object],
    result_type: type[object],
) -> None:
    request = WmsEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key="intent-key",
        expected_result_identity=identity,
    )
    wire = {**_completed_wire(source_version=7), "result_payload": payload}

    snapshot = parse_wms_effect_status_snapshot(request=request, raw_response=wire)

    assert isinstance(snapshot.result, result_type)


@pytest.mark.parametrize(
    "wire",
    [
        {**_completed_wire(), "result_payload": None},
        {**_completed_wire(), "result_payload": {**_completed_wire()["result_payload"], "accepted": False}},
        {**_completed_wire(), "result_payload": {**_completed_wire()["result_payload"], "accepted": 1}},
        {**_completed_wire(), "result_payload": {**_completed_wire()["result_payload"], "accepted": "yes"}},
        {**_completed_wire(), "result_payload": {**_completed_wire()["result_payload"], "extra": "forbidden"}},
        {**_completed_wire(), "result_payload": {**_completed_wire()["result_payload"], "package_id": "OTHER"}},
        {**_completed_wire(), "result_payload": {**_completed_wire()["result_payload"], "source_version": "4"}},
        {**_completed_wire(), "source_version": "3"},
        {**_completed_wire(), "updated_at": 0},
        {**_completed_wire(), "updated_at": "2026-07-24T00:00:00Z"},
        {**_completed_wire(), "updated_at": "2026-07-24T00:00:00+0000"},
        {**_completed_wire(), "state": "PROCESSING"},
        {
            "state": "REJECTED",
            "provider_reference": "wms-effect-001",
            "reason_code": None,
            "updated_at": "2026-07-24T00:00:00+00:00",
            "source_version": 4,
            "result_payload": None,
        },
        {
            "state": "NOT_FOUND",
            "provider_reference": None,
            "reason_code": None,
            "updated_at": None,
            "source_version": 4,
            "result_payload": None,
        },
        {
            "state": "REJECTED",
            "provider_reference": "wms-effect-001",
            "reason_code": "PALLET_LOCKED",
            "updated_at": "2026-07-24T00:00:00+00:00",
            "source_version": 4,
            "result_payload": None,
        },
    ],
)
def test_snapshot_rejects_malformed_terminal_or_correlation_contract(wire: dict[str, object]) -> None:
    with pytest.raises(
        (ValueError, ValidationError),
        match=r"result|accepted|identity|version|reason|NOT_FOUND|updated_at|timestamp",
    ):
        parse_wms_effect_status_snapshot(request=_notify_request(), raw_response=wire)


def test_status_progression_rejects_version_regression_and_same_version_drift() -> None:
    request = _notify_request()
    current = parse_wms_effect_status_snapshot(request=request, raw_response=_completed_wire(source_version=3))
    older = current.model_copy(update={"source_version": 2})
    drifted = current.model_copy(update={"provider_reference": "other"})
    missing = WmsEffectStatusSnapshot.not_found(request)

    with pytest.raises(ValueError, match="regress"):
        assert_status_snapshot_progression(previous=current, current=older)
    with pytest.raises(ValueError, match="same source_version"):
        assert_status_snapshot_progression(previous=current, current=drifted)
    with pytest.raises(ValueError, match="NOT_FOUND"):
        assert_status_snapshot_progression(previous=current, current=missing)
    assert assert_status_snapshot_progression(previous=current, current=current) is current


class _CredentialProvider:
    def resolve(self, credential_reference: str) -> bytes:
        assert credential_reference.endswith("@v1")
        return b"status-test-secret"


class _RecordingStatusEvidenceWriter:
    def __init__(self, *, permit: WmsQueryCallPermit | None = None) -> None:
        self.permit = permit or WmsQueryCallPermit(allowed=True)
        self.before_calls: list[tuple[str, str]] = []
        self.records: list[dict[str, Any]] = []

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        self.before_calls.append((operation_identity, target_code))
        return self.permit

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        request_snapshot: dict[str, object],
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str:
        self.records.append(
            {
                "operation_identity": operation_identity,
                "target_code": target_code,
                "request_snapshot": request_snapshot,
                "outcome": outcome,
                "permit": permit,
            }
        )
        return f"status:{operation_identity}:evidence"


@pytest.mark.asyncio
async def test_adapter_uses_only_frozen_query_key_and_returns_typed_snapshot() -> None:
    requests: list[httpx.Request] = []
    evidence_writer = _RecordingStatusEvidenceWriter()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_completed_wire())

    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=evidence_writer,
        transport=httpx.MockTransport(handler),
        now=lambda: datetime(2026, 7, 24, tzinfo=UTC),
        nonce_factory=lambda: "status-nonce",
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    first = await adapter.query_status(_notify_request())
    second = await adapter.query_status(_notify_request())

    assert first == second
    assert isinstance(first.result, NotifyPackageBindingOperationResult)
    assert dict(requests[0].url.params) == {
        "operation_identity": NOTIFY_PACKAGE_BINDING,
        "idempotency_key": "intent-idempotency-001",
    }
    assert requests[0].headers["X-WMS-Credential-Reference"].endswith("@v1")
    assert requests[0].headers["X-WMS-Signature"]
    assert evidence_writer.before_calls == [(NOTIFY_PACKAGE_BINDING, "WMS_EFFECT_STATUS")] * 2
    assert all(isinstance(record["outcome"], QuerySuccess) for record in evidence_writer.records)


@pytest.mark.asyncio
async def test_runtime_factory_builds_adapter_only_from_frozen_status_binding() -> None:
    observed_urls: list[str] = []
    evidence_writer = _RecordingStatusEvidenceWriter()

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url.copy_with(query=None)))
        return httpx.Response(200, json=_completed_wire())

    factory = build_effect_status_query_port_factory(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=evidence_writer,
        transport=httpx.MockTransport(handler),
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    snapshot = await factory().query_status(_notify_request())

    assert snapshot.state is WmsEffectStatus.COMPLETED
    assert observed_urls == ["https://wms.example/northbound/operations/status"]
    assert evidence_writer.before_calls == [(NOTIFY_PACKAGE_BINDING, "WMS_EFFECT_STATUS")]


def test_runtime_factory_wires_status_query_to_existing_shared_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = _binding()
    evidence_writer = _RecordingStatusEvidenceWriter()
    captured: dict[str, object] = {}

    def build_writer(**kwargs):
        captured.update(kwargs)
        return evidence_writer

    monkeypatch.setattr(runtime_factory, "WmsCallEvidenceQueryWriter", build_writer)

    factory = build_effect_status_query_port_factory(
        binding=binding,
        credential_provider=_CredentialProvider(),
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    assert factory()
    assert captured["provider_profile_identity"] == binding.provider_profile_identity
    assert captured["breaker_service"] is runtime_factory.wms_circuit_breaker_service


@pytest.mark.asyncio
async def test_status_breaker_open_fast_fails_without_calling_transport() -> None:
    transport_calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return httpx.Response(200, json=_completed_wire())

    evidence_writer = _RecordingStatusEvidenceWriter(
        permit=WmsQueryCallPermit(
            allowed=False,
            reason="OPEN_FAST_FAIL",
            retry_after_seconds=7,
        )
    )
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=evidence_writer,
        transport=httpx.MockTransport(handler),
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    with pytest.raises(WmsEffectStatusQueryError) as raised:
        await adapter.query_status(_notify_request())

    assert transport_calls == 0
    assert isinstance(raised.value.failure, QueryTechnicalFailure)
    assert raised.value.failure.reason_code == "WMS_CIRCUIT_OPEN"
    assert raised.value.failure.retry_after_seconds == 7
    assert evidence_writer.before_calls == [(NOTIFY_PACKAGE_BINDING, "WMS_EFFECT_STATUS")]
    assert evidence_writer.records[0]["outcome"].reason_code == raised.value.failure.reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_outcome_type", "expected_reason"),
    [
        ("success", QuerySuccess, None),
        ("timeout", QueryTechnicalFailure, "WMS_PROVIDER_TIMEOUT"),
        ("5xx", QueryTechnicalFailure, "WMS_PROVIDER_UNAVAILABLE"),
    ],
)
async def test_status_query_records_shared_breaker_outcome(
    scenario: str,
    expected_outcome_type: type[object],
    expected_reason: str | None,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if scenario == "timeout":
            raise httpx.ReadTimeout("status timeout", request=request)
        if scenario == "5xx":
            return httpx.Response(503)
        return httpx.Response(200, json=_completed_wire())

    evidence_writer = _RecordingStatusEvidenceWriter()
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=evidence_writer,
        transport=httpx.MockTransport(handler),
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    if scenario == "success":
        snapshot = await adapter.query_status(_notify_request())
        assert snapshot.state is WmsEffectStatus.COMPLETED
    else:
        with pytest.raises(WmsEffectStatusQueryError):
            await adapter.query_status(_notify_request())

    assert evidence_writer.before_calls == [(NOTIFY_PACKAGE_BINDING, "WMS_EFFECT_STATUS")]
    assert evidence_writer.records[0]["operation_identity"] == NOTIFY_PACKAGE_BINDING
    assert evidence_writer.records[0]["target_code"] == "WMS_EFFECT_STATUS"
    outcome = evidence_writer.records[0]["outcome"]
    assert isinstance(outcome, expected_outcome_type)
    assert getattr(outcome, "reason_code", None) == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "failure_type", "reason_code"),
    [
        (httpx.Response(401), QueryContractFailure, "WMS_AUTH_REJECTED"),
        (httpx.Response(403), QueryContractFailure, "WMS_AUTH_REJECTED"),
        (httpx.Response(500), QueryTechnicalFailure, "WMS_PROVIDER_UNAVAILABLE"),
        (httpx.Response(422, json={"code": "IDEMPOTENCY_CONFLICT"}), QueryContractFailure, "WMS_STATUS_HTTP_ERROR"),
    ],
)
async def test_adapter_maps_http_failures_without_interpreting_submit_conflicts(
    response: httpx.Response,
    failure_type: type[object],
    reason_code: str,
) -> None:
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=_RecordingStatusEvidenceWriter(),
        transport=httpx.MockTransport(lambda _request: response),
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    with pytest.raises(WmsEffectStatusQueryError) as raised:
        await adapter.query_status(_notify_request())

    assert isinstance(raised.value.failure, failure_type)
    assert raised.value.failure.reason_code == reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry_after", "attempt_count", "expected_delay"),
    [
        ("12", 1, 8.0),
        ("+12", 2, 2.0),
        ("-0", 2, 2.0),
        (" 12", 2, 2.0),
        ("99999999", 1, 8.0),
        ("9" * 309, 2, 2.0),
        ("Fri, 24 Jul 2026 00:00:06 GMT", 1, 6.0),
        ("invalid", 3, 4.0),
        (None, 9, 8.0),
    ],
)
async def test_rate_limit_uses_valid_retry_after_or_bounded_exponential_fallback(
    retry_after: str | None,
    attempt_count: int,
    expected_delay: float,
) -> None:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=_RecordingStatusEvidenceWriter(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(429, headers=headers)),
        now=lambda: datetime(2026, 7, 24, tzinfo=UTC),
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    with pytest.raises(WmsEffectStatusQueryError) as raised:
        await adapter.query_status(_notify_request(attempt_count=attempt_count))

    assert isinstance(raised.value.failure, QueryTechnicalFailure)
    assert raised.value.failure.reason_code == "WMS_RATE_LIMITED"
    assert raised.value.failure.retry_after_seconds == expected_delay


@pytest.mark.asyncio
async def test_default_rate_limit_backoff_adds_bounded_random_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    sampled_bounds: list[tuple[float, float]] = []

    def sample(lower: float, upper: float) -> float:
        sampled_bounds.append((lower, upper))
        return upper / 2

    monkeypatch.setattr(effect_status_query_adapter._BACKOFF_RANDOM, "uniform", sample)
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=_RecordingStatusEvidenceWriter(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(429)),
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    with pytest.raises(WmsEffectStatusQueryError) as raised:
        await adapter.query_status(_notify_request(attempt_count=2))

    assert sampled_bounds == [(0.0, 2.0)]
    assert isinstance(raised.value.failure, QueryTechnicalFailure)
    assert raised.value.failure.retry_after_seconds == 3.0


def test_local_backoff_caps_before_exponentiation_for_huge_attempt_count() -> None:
    jitter_calls: list[float] = []
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=_RecordingStatusEvidenceWriter(),
        jitter=lambda upper: jitter_calls.append(upper) or upper,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    assert adapter._local_backoff(1_000_000) == 8.0
    assert jitter_calls == []


@pytest.mark.asyncio
async def test_status_timeout_is_one_absolute_deadline_including_stream_read() -> None:
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"{}"

        async def aclose(self) -> None:
            await asyncio.sleep(0.2)

    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(timeout_seconds=0.01),
        credential_provider=_CredentialProvider(),
        evidence_writer=_RecordingStatusEvidenceWriter(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=SlowBody())),
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    started_at = asyncio.get_running_loop().time()
    with pytest.raises(WmsEffectStatusQueryError) as raised:
        await adapter.query_status(_notify_request())
    elapsed = asyncio.get_running_loop().time() - started_at

    assert isinstance(raised.value.failure, QueryTechnicalFailure)
    assert raised.value.failure.reason_code == "WMS_PROVIDER_TIMEOUT"
    assert elapsed < 0.1


def test_status_adapter_reuses_shared_http_transport_primitives() -> None:
    source = inspect.getsource(effect_status_query_adapter)

    assert "httpx.AsyncClient(" not in source
    assert "hmac.new(" not in source
    assert "aiter_bytes(" not in source
    assert "open_wms_http_client" in source
    assert "send_bounded_wms_request" in source
    assert "sign_wms_hmac_request" in source


@pytest.mark.asyncio
async def test_repeated_status_queries_do_not_close_the_injected_transport() -> None:
    class CloseAwareTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.closed = False
            self.calls = 0

        async def handle_async_request(self, _request: httpx.Request) -> httpx.Response:
            if self.closed:
                raise RuntimeError("transport already closed")
            self.calls += 1
            return httpx.Response(200, json=_completed_wire())

        async def aclose(self) -> None:
            self.closed = True

    transport = CloseAwareTransport()
    adapter = WmsEffectStatusQueryAdapter(
        binding=_binding(),
        credential_provider=_CredentialProvider(),
        evidence_writer=_RecordingStatusEvidenceWriter(),
        transport=transport,
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )

    first = await adapter.query_status(_notify_request())
    second = await adapter.query_status(_notify_request())

    assert first == second
    assert transport.calls == 2
    assert transport.closed is False
    await transport.aclose()
    assert transport.closed is True
