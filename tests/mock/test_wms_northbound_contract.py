"""WMS 北向 Mock 的认证、幂等与状态核心合同测试。"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest

from tests.mock import wms_mock_server
from tests.mock.wms_northbound_contract import (
    MOCK_NORTHBOUND_CREDENTIAL_REFERENCE,
    MOCK_NORTHBOUND_HMAC_SECRET_ENV,
    NorthboundAuthError,
    NorthboundOperationStore,
    build_package_binding_result,
    canonical_status_string,
    canonical_submit_string,
    content_sha256,
    resolve_mock_northbound_credential,
    verify_status_hmac,
    verify_submit_hmac,
)


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
        "X-WES-Credential-Reference": MOCK_NORTHBOUND_CREDENTIAL_REFERENCE,
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
        "X-WMS-Credential-Reference": MOCK_NORTHBOUND_CREDENTIAL_REFERENCE,
        "X-WMS-Signature-Algorithm": "HMAC_SHA256",
        "X-WMS-Timestamp": timestamp,
        "X-WMS-Nonce": nonce,
        "X-WMS-Content-SHA256": payload_hash,
        "X-WMS-Signature": hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest(),
    }


def _payload(*, station_code: str = "station-a") -> dict[str, str]:
    return {
        "dispatch_key": "dispatch-001",
        "package_id": "package-001",
        "pallet_id": "pallet-001",
        "station_code": station_code,
    }


def test_resolve_mock_credential_accepts_only_versioned_allowlisted_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MOCK_NORTHBOUND_HMAC_SECRET_ENV, "mock-hmac-secret")

    assert resolve_mock_northbound_credential(MOCK_NORTHBOUND_CREDENTIAL_REFERENCE) == b"mock-hmac-secret"
    with pytest.raises(NorthboundAuthError, match="CREDENTIAL_REFERENCE_REJECTED"):
        resolve_mock_northbound_credential("secret://wms/other@v1")


def test_submit_hmac_rejects_tampered_content_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MOCK_NORTHBOUND_HMAC_SECRET_ENV, secret.decode())
    body = b'{"dispatch_key":"dispatch-001"}'

    with pytest.raises(NorthboundAuthError, match="CONTENT_HASH_MISMATCH"):
        verify_submit_hmac(
            _submit_headers(body=body, secret=secret, content_hash="0" * 64),
            body,
            method="POST",
            path="/northbound/operations",
        )


def test_submit_hmac_rejects_signature_not_matching_canonical_field_order(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MOCK_NORTHBOUND_HMAC_SECRET_ENV, secret.decode())
    body = b'{"dispatch_key":"dispatch-001"}'
    headers = _submit_headers(body=body, secret=secret)
    headers["X-WES-Signature"] = "0" * 64

    with pytest.raises(NorthboundAuthError, match="INVALID_HMAC_SIGNATURE"):
        verify_submit_hmac(headers, body, method="POST", path="/northbound/operations")


def test_status_hmac_uses_received_raw_query_path(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"mock-hmac-secret"
    monkeypatch.setenv(MOCK_NORTHBOUND_HMAC_SECRET_ENV, secret.decode())
    path = "/northbound/operations/status?operation_identity=wms.fulfillment.notify_pkg_binding%40v1&idempotency_key=idem-001"
    headers = _status_headers(secret=secret, path=path)

    verify_status_hmac(headers, b"", method="GET", path=path)
    with pytest.raises(NorthboundAuthError, match="INVALID_HMAC_SIGNATURE"):
        verify_status_hmac(headers, b"", method="GET", path=path.replace("%40", "@"))


def test_store_scopes_idempotency_by_operation_identity_and_replays_completed_typed_result() -> None:
    store = NorthboundOperationStore(clock=lambda: datetime(2026, 7, 25, tzinfo=UTC))
    payload = _payload()
    fingerprint = content_sha256(b'{"dispatch_key":"dispatch-001"}')

    first = store.submit("wms.fulfillment.notify_pkg_binding@v1", "idem-001", fingerprint, payload)
    other_operation = store.submit("wms.fulfillment.full_box_exchange@v1", "idem-001", fingerprint, payload)
    accepted = store.query("wms.fulfillment.notify_pkg_binding@v1", "idem-001")
    processing = store.query("wms.fulfillment.notify_pkg_binding@v1", "idem-001")
    completed = store.query("wms.fulfillment.notify_pkg_binding@v1", "idem-001")
    replay = store.submit("wms.fulfillment.notify_pkg_binding@v1", "idem-001", fingerprint, payload)

    assert first.status_code == 202
    assert other_operation.status_code == 202
    assert [accepted.state, processing.state, completed.state] == ["ACCEPTED", "PROCESSING", "COMPLETED"]
    assert [accepted.source_version, processing.source_version, completed.source_version] == [0, 1, 2]
    assert replay.status_code == 200
    assert replay.snapshot == completed
    assert completed.result_payload == build_package_binding_result(
        payload, source_version=2, completed_at=completed.updated_at
    )


def test_store_rejects_same_key_with_different_fingerprint_without_another_effect() -> None:
    store = NorthboundOperationStore()
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    store.submit(operation_identity, "idem-001", "a" * 64, _payload())

    conflict = store.submit(operation_identity, "idem-001", "b" * 64, _payload(station_code="station-b"))

    assert conflict.status_code == 422
    assert conflict.error_code == "IDEMPOTENCY_CONFLICT"
    assert store.effect_count(operation_identity, "idem-001") == 1


def test_store_replays_processing_and_can_record_rejection_and_not_found() -> None:
    store = NorthboundOperationStore()
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    store.submit(operation_identity, "idem-001", "a" * 64, _payload())

    in_progress = store.submit(operation_identity, "idem-001", "a" * 64, _payload())
    rejected = store.reject(operation_identity, "idem-001", reason_code="WMS_BUSINESS_REJECTED")
    missing = store.query(operation_identity, "missing")

    assert in_progress.status_code == 409
    assert in_progress.error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert rejected.state == "REJECTED"
    assert rejected.reason_code == "WMS_BUSINESS_REJECTED"
    assert rejected.result_payload is None
    assert missing.state == "NOT_FOUND"
    assert missing.source_version is None
    assert missing.updated_at is None


def test_callback_hint_is_registered_once_and_reset_removes_northbound_records() -> None:
    store = NorthboundOperationStore()
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    store.submit(operation_identity, "idem-001", "a" * 64, _payload())

    assert store.register_callback_hint(operation_identity, "idem-001") is True
    assert store.register_callback_hint(operation_identity, "idem-001") is False
    store.reset()

    assert store.query(operation_identity, "idem-001").state == "NOT_FOUND"
    assert store.register_callback_hint(operation_identity, "idem-001") is False


def test_wms_mock_reset_clears_shared_northbound_operation_store() -> None:
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    wms_mock_server.northbound_operation_store.submit(operation_identity, "idem-reset", "a" * 64, _payload())

    wms_mock_server.reset_mock_wms_state()

    assert wms_mock_server.northbound_operation_store.query(operation_identity, "idem-reset").state == "NOT_FOUND"
