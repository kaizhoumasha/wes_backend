"""WMS 北向 Mock 的认证、幂等与状态核心合同测试。"""

from __future__ import annotations

import hashlib
import hmac
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationDefinition, WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from tests.mock import wms_mock_server
from tests.mock.wms_northbound_contract import (
    ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
    MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V1,
    MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V2,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2,
    NorthboundAuthError,
    NorthboundOperationStore,
    build_typed_result,
    canonical_status_string,
    canonical_submit_string,
    content_sha256,
    resolve_mock_northbound_credential,
    verify_status_hmac,
    verify_submit_hmac,
)
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

ASYNC_RACK_SUPPLY = "wms.fulfillment.request_rack_supply@v1"
ASYNC_RACK_TRANSPORT = "wms.fulfillment.request_rack_transport@v1"


def _operation_payload(operation_identity: str) -> dict[str, object]:
    return deepcopy(REQUEST_FIXTURES[operation_identity])


def _submit_headers(*, body: bytes, secret: bytes, content_hash: str | None = None) -> dict[str, str]:
    timestamp = "1721865600"
    nonce = "submit-nonce"
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    idempotency_key = "idem-001"
    payload_hash = content_hash or content_sha256(body)
    canonical = canonical_submit_string(
        method="POST",
        path="/northbound/operations",
        timestamp=timestamp,
        nonce=nonce,
        payload_hash=payload_hash,
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
    )
    return {
        "X-WES-Credential-Reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "X-WES-Signature-Algorithm": "HMAC_SHA256",
        "X-WES-Timestamp": timestamp,
        "X-WES-Nonce": nonce,
        "X-WES-Content-SHA256": payload_hash,
        "X-WES-Operation-Identity": operation_identity,
        "Idempotency-Key": idempotency_key,
        "X-WES-Signature": hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest(),
    }


def _status_headers(*, secret: bytes, path: str) -> dict[str, str]:
    timestamp = "1721865601"
    nonce = "status-nonce"
    payload_hash = content_sha256(b"")
    canonical = canonical_status_string(
        method="GET", path=path, timestamp=timestamp, nonce=nonce, payload_hash=payload_hash
    )
    return {
        "X-WMS-Credential-Reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "X-WMS-Signature-Algorithm": "HMAC_SHA256",
        "X-WMS-Timestamp": timestamp,
        "X-WMS-Nonce": nonce,
        "X-WMS-Content-SHA256": payload_hash,
        "X-WMS-Signature": hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest(),
    }


def _payload(*, station_code: str = "station-a") -> dict[str, str]:
    return {
        "dispatch_key": "dispatch-001",
        "pkg_id": "package-001",
        "bin_id": "bin-001",
        "slot_id": "slot-001",
        "rack_id": "rack-001",
        "station_code": station_code,
    }


def test_operation_store_accepts_fractional_visibility_sla() -> None:
    clock = {"now": datetime(2026, 7, 25, tzinfo=UTC)}
    store = NorthboundOperationStore(
        clock=lambda: clock["now"],
        retention_seconds=9,
        visibility_sla_seconds=2.5,
    )

    store.configure_visibility_delay(
        ASYNC_RACK_SUPPLY,
        "idem-fractional-visibility",
        delay_seconds=2.5,
    )
    store.submit(
        ASYNC_RACK_SUPPLY,
        "idem-fractional-visibility",
        "a" * 64,
        _operation_payload(ASYNC_RACK_SUPPLY),
    )
    clock["now"] += timedelta(seconds=2.49)
    assert store.query(ASYNC_RACK_SUPPLY, "idem-fractional-visibility").state == "NOT_FOUND"
    clock["now"] += timedelta(seconds=0.01)
    assert store.query(ASYNC_RACK_SUPPLY, "idem-fractional-visibility").state == "ACCEPTED"


@pytest.mark.parametrize(
    "operation",
    tuple(operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.SYNC_RESULT),
    ids=lambda operation: operation.identity,
)
def test_sync_effect_returns_terminal_result_without_status_or_callback_hint(
    operation: WmsOperationDefinition,
) -> None:
    store = NorthboundOperationStore(clock=lambda: datetime(2026, 7, 25, tzinfo=UTC))
    payload = REQUEST_FIXTURES[operation.identity]

    submission = store.submit(operation.identity, "idem-sync", "a" * 64, payload)

    assert submission.status_code == 200
    assert submission.snapshot is not None
    assert submission.snapshot.state == "COMPLETED"
    operation.result_model.model_validate(submission.snapshot.result_payload)
    assert store.register_callback_hint(operation.identity, "idem-sync") is False
    with pytest.raises(ValueError, match="ASYNC_TASK"):
        store.query(operation.identity, "idem-sync")


def test_resolve_mock_credential_accepts_only_versioned_allowlisted_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1, "mock-hmac-secret-v1")
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, "mock-hmac-secret-v2")

    assert resolve_mock_northbound_credential(MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V1) == b"mock-hmac-secret-v1"
    assert resolve_mock_northbound_credential(MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V2) == b"mock-hmac-secret-v2"
    with pytest.raises(NorthboundAuthError, match="CREDENTIAL_REFERENCE_REJECTED"):
        resolve_mock_northbound_credential("secret://wms/other@v1")


def test_submit_hmac_rejects_tampered_content_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret.decode())
    body = b'{"dispatch_key":"dispatch-001"}'

    with pytest.raises(NorthboundAuthError, match="CONTENT_HASH_MISMATCH"):
        verify_submit_hmac(
            _submit_headers(body=body, secret=secret, content_hash="0" * 64),
            body,
            method="POST",
            path="/northbound/operations",
        )


def test_submit_hmac_rejects_header_whitespace_even_when_signature_uses_trimmed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret.decode())
    body = b'{"dispatch_key":"dispatch-001"}'
    headers = _submit_headers(body=body, secret=secret)
    headers["Idempotency-Key"] = "  idem-001  "

    with pytest.raises(NorthboundAuthError, match="MISSING_OR_INVALID_AUTH_HEADER"):
        verify_submit_hmac(headers, body, method="POST", path="/northbound/operations")


def test_submit_hmac_rejects_signature_not_matching_canonical_field_order(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret.decode())
    body = b'{"dispatch_key":"dispatch-001"}'
    headers = _submit_headers(body=body, secret=secret)
    headers["X-WES-Signature"] = "0" * 64

    with pytest.raises(NorthboundAuthError, match="INVALID_HMAC_SIGNATURE"):
        verify_submit_hmac(headers, body, method="POST", path="/northbound/operations")


def test_status_hmac_uses_received_raw_query_path(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret.decode())
    path = "/northbound/operations/status?operation_identity=wms.fulfillment.notify_pkg_binding%40v1&idempotency_key=idem-001"
    headers = _status_headers(secret=secret, path=path)

    verify_status_hmac(headers, b"", method="GET", path=path)
    with pytest.raises(NorthboundAuthError, match="INVALID_HMAC_SIGNATURE"):
        verify_status_hmac(headers, b"", method="GET", path=path.replace("%40", "@"))


def test_status_hmac_rejects_non_empty_body_even_when_hash_and_signature_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, secret.decode())
    path = "/northbound/operations/status?operation_identity=op&idempotency_key=idem-001"
    body = b"unexpected"
    payload_hash = content_sha256(body)
    timestamp = "1721865601"
    nonce = "status-nonce"
    canonical = canonical_status_string(
        method="GET",
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        payload_hash=payload_hash,
    )
    headers = {
        "X-WMS-Credential-Reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "X-WMS-Signature-Algorithm": "HMAC_SHA256",
        "X-WMS-Timestamp": timestamp,
        "X-WMS-Nonce": nonce,
        "X-WMS-Content-SHA256": payload_hash,
        "X-WMS-Signature": hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest(),
    }

    with pytest.raises(NorthboundAuthError, match="CONTENT_HASH_MISMATCH"):
        verify_status_hmac(headers, body, method="GET", path=path)


def test_store_scopes_idempotency_by_operation_identity_and_replays_completed_typed_result() -> None:
    store = NorthboundOperationStore(clock=lambda: datetime(2026, 7, 25, tzinfo=UTC))
    payload = _operation_payload(ASYNC_RACK_SUPPLY)
    other_payload = _operation_payload(ASYNC_RACK_TRANSPORT)
    fingerprint = content_sha256(b'{"dispatch_key":"dispatch-001"}')

    first = store.submit(ASYNC_RACK_SUPPLY, "idem-001", fingerprint, payload)
    other_operation = store.submit(ASYNC_RACK_TRANSPORT, "idem-001", fingerprint, other_payload)
    accepted = store.query(ASYNC_RACK_SUPPLY, "idem-001")
    processing = store.query(ASYNC_RACK_SUPPLY, "idem-001")
    completed = store.query(ASYNC_RACK_SUPPLY, "idem-001")
    replay = store.submit(ASYNC_RACK_SUPPLY, "idem-001", fingerprint, payload)

    assert first.status_code == 202
    assert other_operation.status_code == 202
    assert [accepted.state, processing.state, completed.state] == ["ACCEPTED", "PROCESSING", "COMPLETED"]
    assert [accepted.source_version, processing.source_version, completed.source_version] == [0, 1, 2]
    assert replay.status_code == 200
    assert replay.snapshot == completed
    assert completed.result_payload == build_typed_result(
        ASYNC_RACK_SUPPLY,
        payload,
        source_version=2,
        completed_at=completed.updated_at,
        provider_reference=completed.provider_reference,
    )


def test_store_rejects_same_key_with_different_fingerprint_without_another_effect() -> None:
    store = NorthboundOperationStore()
    operation_identity = ASYNC_RACK_SUPPLY
    payload = _operation_payload(operation_identity)
    store.submit(operation_identity, "idem-001", "a" * 64, payload)

    conflict = store.submit(operation_identity, "idem-001", "b" * 64, payload)

    assert conflict.status_code == 422
    assert conflict.error_code == "IDEMPOTENCY_CONFLICT"
    assert store.effect_count(operation_identity, "idem-001") == 1


def test_store_replays_processing_and_can_record_rejection_and_not_found() -> None:
    store = NorthboundOperationStore()
    operation_identity = ASYNC_RACK_SUPPLY
    payload = _operation_payload(operation_identity)
    store.submit(operation_identity, "idem-001", "a" * 64, payload)

    in_progress = store.submit(operation_identity, "idem-001", "a" * 64, payload)
    rejected = store.reject(operation_identity, "idem-001", reason_code="NO_RACK_AVAILABLE")
    missing = store.query(operation_identity, "missing")

    assert in_progress.status_code == 409
    assert in_progress.error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert rejected.state == "REJECTED"
    assert rejected.reason_code == "NO_RACK_AVAILABLE"
    assert rejected.result_payload is None
    assert missing.state == "NOT_FOUND"
    assert missing.source_version is None
    assert missing.updated_at is None


@pytest.mark.parametrize(
    "operation",
    tuple(operation for operation in WMS_OPERATIONS if operation.mode is WmsOperationMode.EFFECT),
    ids=lambda operation: operation.identity,
)
def test_completed_result_builder_matches_every_frozen_effect_schema(operation: WmsOperationDefinition) -> None:
    payload = REQUEST_FIXTURES[operation.identity]

    result = build_typed_result(
        operation.identity,
        payload,
        source_version=3,
        completed_at="2026-07-24T00:00:02+00:00",
    )

    operation.result_model.model_validate(result)
    assert result["dispatch_key"] == payload["dispatch_key"]


@pytest.mark.parametrize(
    ("operation_identity", "reason_code"),
    (
        (ASYNC_RACK_SUPPLY, "NO_RACK_AVAILABLE"),
        (ASYNC_RACK_TRANSPORT, "RACK_NOT_FOUND"),
        ("wms.fulfillment.change_rack_face@v1", "FACE_CHANGE_BLOCKED"),
    ),
)
def test_store_reject_accepts_only_reason_code_frozen_for_each_operation(
    operation_identity: str, reason_code: str
) -> None:
    store = NorthboundOperationStore()
    store.submit(operation_identity, "idem-reject", "a" * 64, _operation_payload(operation_identity))

    rejected = store.reject(operation_identity, "idem-reject", reason_code=reason_code)

    assert rejected.state == "REJECTED"
    assert rejected.reason_code == reason_code


@pytest.mark.parametrize(
    ("operation_identity", "reason_code"),
    (
        (ASYNC_RACK_SUPPLY, "RACK_NOT_FOUND"),
        (ASYNC_RACK_TRANSPORT, "NO_RACK_AVAILABLE"),
        ("wms.fulfillment.change_rack_face@v1", "NO_RACK_AVAILABLE"),
    ),
)
def test_store_reject_rejects_reason_code_frozen_for_another_operation(
    operation_identity: str, reason_code: str
) -> None:
    store = NorthboundOperationStore()
    store.submit(operation_identity, "idem-reject", "a" * 64, _operation_payload(operation_identity))

    with pytest.raises(ValueError, match="reason_code is not allowed"):
        store.reject(operation_identity, "idem-reject", reason_code=reason_code)


def test_callback_hint_is_registered_once_and_reset_removes_northbound_records() -> None:
    store = NorthboundOperationStore()
    operation_identity = ASYNC_RACK_SUPPLY
    store.submit(operation_identity, "idem-001", "a" * 64, _operation_payload(operation_identity))

    assert store.register_callback_hint(operation_identity, "idem-001") is True
    assert store.register_callback_hint(operation_identity, "idem-001") is False
    store.reset()

    assert store.query(operation_identity, "idem-001").state == "NOT_FOUND"
    assert store.register_callback_hint(operation_identity, "idem-001") is False


def test_wms_mock_reset_clears_shared_northbound_operation_store() -> None:
    operation_identity = ASYNC_RACK_SUPPLY
    wms_mock_server.northbound_operation_store.submit(
        operation_identity,
        "idem-reset",
        "a" * 64,
        _operation_payload(operation_identity),
    )

    wms_mock_server.reset_mock_wms_state()

    assert wms_mock_server.northbound_operation_store.query(operation_identity, "idem-reset").state == "NOT_FOUND"


def test_mock_credential_uses_material_flow_sandbox_rotation_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock 必须接受真实 WES active v2 与冻结重试 v1，拒绝已废弃的 mock 专用 reference。"""

    monkeypatch.setenv("WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1", "material-flow-v1")
    monkeypatch.setenv("WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2", "material-flow-v2")
    monkeypatch.setenv("MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1", "obsolete-mock-secret")

    assert resolve_mock_northbound_credential("secret://wms/material-flow-sandbox-hmac@v1") == b"material-flow-v1"
    assert resolve_mock_northbound_credential("secret://wms/material-flow-sandbox-hmac@v2") == b"material-flow-v2"
    with pytest.raises(NorthboundAuthError, match="CREDENTIAL_REFERENCE_REJECTED"):
        resolve_mock_northbound_credential("secret://wms/mock-northbound-hmac@v1")


def test_store_visibility_and_retention_follow_clock_boundaries() -> None:
    operation_identity = ASYNC_RACK_SUPPLY
    idempotency_key = "idem-clock-boundary-001"
    started_at = datetime(2026, 7, 25, tzinfo=UTC)
    current = [started_at]
    store = NorthboundOperationStore(
        clock=lambda: current[0],
        retention_seconds=9,
        visibility_sla_seconds=2,
    )
    store.configure_visibility_delay(operation_identity, idempotency_key, delay_seconds=2)

    payload = _operation_payload(operation_identity)
    first = store.submit(operation_identity, idempotency_key, "a" * 64, payload)
    hidden_at_accept = store.query(operation_identity, idempotency_key)
    current[0] = started_at + timedelta(seconds=1)
    hidden_before_sla = store.query(operation_identity, idempotency_key)
    current[0] = started_at + timedelta(seconds=2)
    visible_at_sla = store.query(operation_identity, idempotency_key)
    current[0] = started_at + timedelta(seconds=8)
    replay_before_boundary = store.submit(operation_identity, idempotency_key, "a" * 64, payload)
    current[0] = started_at + timedelta(seconds=9)
    expired_at_boundary = store.query(operation_identity, idempotency_key)
    recovered_at_boundary = store.submit(operation_identity, idempotency_key, "a" * 64, payload)

    assert first.status_code == 202
    assert hidden_at_accept.state == "NOT_FOUND"
    assert hidden_before_sla.state == "NOT_FOUND"
    assert visible_at_sla.state == "ACCEPTED"
    assert replay_before_boundary.status_code == 409
    assert store.effect_count(operation_identity, idempotency_key) == 2
    assert expired_at_boundary.state == "NOT_FOUND"
    assert recovered_at_boundary.status_code == 202


def test_store_is_immediately_visible_without_mock_visibility_control() -> None:
    store = NorthboundOperationStore(
        clock=lambda: datetime(2026, 7, 25, tzinfo=UTC),
        retention_seconds=9,
        visibility_sla_seconds=2,
    )
    operation_identity = ASYNC_RACK_SUPPLY

    store.submit(
        operation_identity,
        "idem-immediately-visible",
        "a" * 64,
        _operation_payload(operation_identity),
    )

    assert store.query(operation_identity, "idem-immediately-visible").state == "ACCEPTED"
