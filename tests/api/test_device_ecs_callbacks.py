"""统一 ECS callback API facade。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.app.device.contracts import DeviceEvidenceReceipt
from src.app.device.services.device_evidence_service import (
    DeviceEvidenceConflictError,
    DeviceResultConflictError,
    DeviceResultOutOfOrderError,
    UnknownDeviceCommandError,
)
from src.app.device.v1.ecs_callback import router


@dataclass
class FakeEvidenceService:
    failure: Exception | None = None
    duplicate: bool = False

    async def accept_result(self, result):
        if self.failure is not None:
            raise self.failure
        return DeviceEvidenceReceipt(1, f"RESULT:{result.command_code}", self.duplicate, None, "PENDING")

    async def accept_event(self, event):
        if self.failure is not None:
            raise self.failure
        return DeviceEvidenceReceipt(2, "EVENT:derived", self.duplicate, None, "PENDING")


class FakePublisher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []
        self.error = error

    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        self.events.append((channel, event_type, payload))
        if self.error is not None:
            raise self.error
        return True


def _client(
    service: FakeEvidenceService | None = None,
    *,
    publisher: FakePublisher | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    app = FastAPI()
    app.state.device_evidence_service = service or FakeEvidenceService()
    app.state.device_event_stream_service = publisher or FakePublisher()
    app.include_router(router, prefix="/api/v1/callback")
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _result_payload() -> dict[str, object]:
    return {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "result": "SUCCESS",
        "finish_time": 1_786_579_204_000,
        "data": {},
        "error_detail": None,
    }


def _event_payload() -> dict[str, object]:
    return {
        "device_code": "ARM-01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_786_579_204_000,
        "data": {},
    }


def test_result_and_event_ack_only_after_service_returns() -> None:
    publisher = FakePublisher()
    with _client(publisher=publisher) as client:
        result = client.post("/api/v1/callback/result", json=_result_payload())
        event = client.post("/api/v1/callback/event", json=_event_payload())

    assert result.status_code == 200
    assert result.json() == {"code": 200, "message": "ACK"}
    assert event.status_code == 200
    assert event.json() == {"code": 200, "message": "ACK"}
    result_attempt = publisher.events[0][2]
    event_attempt = publisher.events[1][2]
    assert publisher.events[0][:2] == ("device:evidence:stream", "device_ingress.attempted")
    assert result_attempt["kind"] == "DEVICE_RESULT"
    assert result_attempt["disposition"] == "ACCEPTED"
    assert result_attempt["status_code"] == 200
    assert result_attempt["raw_payload"] == _result_payload()
    assert result_attempt["evidence_id"] == 1
    assert result_attempt["apply_status"] == "PENDING"
    assert event_attempt["kind"] == "DEVICE_EVENT"
    assert event_attempt["raw_payload"] == _event_payload()
    assert result_attempt["request_id"] != event_attempt["request_id"]


def test_duplicate_callback_is_a_distinct_attempt_for_same_evidence() -> None:
    publisher = FakePublisher()
    with _client(FakeEvidenceService(duplicate=True), publisher=publisher) as client:
        first = client.post("/api/v1/callback/result", json=_result_payload())
        second = client.post("/api/v1/callback/result", json=_result_payload())

    assert (first.status_code, second.status_code) == (200, 200)
    attempts = [event[2] for event in publisher.events]
    assert [attempt["disposition"] for attempt in attempts] == ["DUPLICATE", "DUPLICATE"]
    assert {attempt["evidence_id"] for attempt in attempts} == {1}
    assert attempts[0]["request_id"] != attempts[1]["request_id"]


def test_body_limit_is_checked_before_json_decode() -> None:
    publisher = FakePublisher()
    with _client(publisher=publisher) as client:
        response = client.post(
            "/api/v1/callback/result",
            content=b"{" + b"x" * (256 * 1024),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["code"] == 413
    attempt = publisher.events[0][2]
    assert attempt["raw_payload"] is None
    assert attempt["observed_body_bytes"] == 256 * 1024 + 1


def test_unknown_command_and_identity_conflict_are_explicit() -> None:
    missing_publisher = FakePublisher()
    conflict_publisher = FakePublisher()
    with _client(
        FakeEvidenceService(UnknownDeviceCommandError("CMD-001")),
        publisher=missing_publisher,
    ) as client:
        missing = client.post("/api/v1/callback/result", json=_result_payload())
    conflict_error = DeviceEvidenceConflictError("RESULT-001")
    conflict_error.receipt = DeviceEvidenceReceipt(9, "RESULT:CMD-001", False, None, "IGNORED")
    with _client(
        FakeEvidenceService(conflict_error),
        publisher=conflict_publisher,
    ) as client:
        conflict = client.post("/api/v1/callback/result", json=_result_payload())

    assert missing.status_code == 404
    assert missing.json()["message"] == "COMMAND_NOT_FOUND"
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "IDEMPOTENCY_CONFLICT"
    assert missing_publisher.events[0][2]["disposition"] == "REJECTED"
    assert conflict_publisher.events[0][2]["disposition"] == "CONFLICT"
    assert conflict_publisher.events[0][2]["raw_payload"] == _result_payload()
    assert conflict_publisher.events[0][2]["evidence_id"] == 9
    assert conflict_publisher.events[0][2]["source_event_id"] == "RESULT:CMD-001"
    assert conflict_publisher.events[0][2]["apply_status"] == "IGNORED"


@pytest.mark.parametrize("error_type", [DeviceEvidenceConflictError, DeviceResultConflictError])
def test_persisted_result_rejection_keeps_evidence_identity_in_attempt(error_type: type[ValueError]) -> None:
    error = error_type("RESULT:CMD-001")
    error.receipt = DeviceEvidenceReceipt(9, "RESULT:CMD-001", False, None, "IGNORED")
    publisher = FakePublisher()

    with _client(FakeEvidenceService(error), publisher=publisher) as client:
        response = client.post("/api/v1/callback/result", json=_result_payload())

    assert response.status_code == 409
    attempt = publisher.events[0][2]
    assert (attempt["evidence_id"], attempt["source_event_id"], attempt["apply_status"]) == (
        9,
        "RESULT:CMD-001",
        "IGNORED",
    )


def test_result_before_dispatch_returns_specific_conflict_and_keeps_attempt_identity() -> None:
    error = DeviceResultOutOfOrderError("RESULT:CMD-001")
    error.receipt = DeviceEvidenceReceipt(9, "RESULT:CMD-001", False, None, "IGNORED")
    publisher = FakePublisher()

    with _client(FakeEvidenceService(error), publisher=publisher) as client:
        response = client.post("/api/v1/callback/result", json=_result_payload())

    assert response.status_code == 409
    assert response.json() == {"code": 409, "message": "RESULT_BEFORE_DISPATCH"}
    assert publisher.events[0][2]["evidence_id"] == 9


@pytest.mark.parametrize("path", ["/api/v1/callback/result", "/api/v1/callback/event"])
def test_diagnostic_attempt_redacts_nested_credentials_without_rejecting_payload(path: str) -> None:
    payload = _result_payload() if path.endswith("result") else _event_payload()
    payload["data"] = {
        "authorization": "Bearer secret",
        "nested": [
            {
                "access_token": "token-value",
                "accessToken": "camel-token-value",
                "app_secret": "app-secret-value",
                "clientSecret": "camel-secret-value",
                "client-secret": "client-secret-value",
                "client_password": "password-value",
                "sessionCookie": "cookie-value",
                "authorization_header": "Basic secret",
                "apikey": "compact-api-key-value",
                "APIKey": "acronym-api-key-value",
                "x-api-key": "api-key-value",
                "business_value": 7,
            }
        ],
    }
    publisher = FakePublisher()

    with _client(publisher=publisher) as client:
        response = client.post(path, json=payload)

    assert response.status_code == 200
    diagnostic_data = publisher.events[0][2]["raw_payload"]["data"]
    assert diagnostic_data == {
        "authorization": "[REDACTED]",
        "nested": [
            {
                "access_token": "[REDACTED]",
                "accessToken": "[REDACTED]",
                "app_secret": "[REDACTED]",
                "clientSecret": "[REDACTED]",
                "client-secret": "[REDACTED]",
                "client_password": "[REDACTED]",
                "sessionCookie": "[REDACTED]",
                "authorization_header": "[REDACTED]",
                "apikey": "[REDACTED]",
                "APIKey": "[REDACTED]",
                "x-api-key": "[REDACTED]",
                "business_value": 7,
            }
        ],
    }


def test_publish_failure_does_not_change_callback_response() -> None:
    with _client(publisher=FakePublisher(error=RuntimeError("redis down"))) as client:
        response = client.post("/api/v1/callback/event", json=_event_payload())

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "ACK"}


def test_unavailable_callback_service_uses_closed_wire() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/callback")
    with TestClient(app) as client:
        unavailable = client.post("/api/v1/callback/result", json=_result_payload())

    assert unavailable.status_code == 503
    assert unavailable.json() == {"code": 503, "message": "TEMPORARILY_UNAVAILABLE"}


def test_closed_envelope_rejects_legacy_or_flattened_fields() -> None:
    payload = {**_result_payload(), "contract_key": "arm.pick"}
    publisher = FakePublisher()
    with _client(publisher=publisher) as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json()["message"] == "INVALID_ENVELOPE"
    attempt = publisher.events[0][2]
    assert attempt["raw_payload"] is None
    assert attempt["observed_body_bytes"] > 0


@pytest.mark.parametrize(
    ("path", "payload", "field"),
    [
        ("/api/v1/callback/result", _result_payload(), "actual_qty"),
        ("/api/v1/callback/event", _event_payload(), "device_business_field"),
    ],
)
def test_business_fields_must_remain_nested(path: str, payload: dict[str, object], field: str) -> None:
    with _client() as client:
        response = client.post(path, json={**payload, field: "flattened"})

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


@pytest.mark.parametrize(
    ("path", "payload", "field"),
    [
        ("/api/v1/callback/result", _result_payload(), "finish_time"),
        ("/api/v1/callback/event", _event_payload(), "timestamp"),
    ],
)
def test_callback_times_require_unix_milliseconds(path: str, payload: dict[str, object], field: str) -> None:
    with _client() as client:
        response = client.post(path, json={**payload, field: "2026-08-13T00:00:04Z"})

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


def test_event_accepts_device_specific_business_data_inside_data() -> None:
    payload = _event_payload()
    payload["data"] = {"device_defined": {"value": "opaque"}}
    with _client() as client:
        response = client.post("/api/v1/callback/event", json=payload)

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "ACK"}


@pytest.mark.parametrize("path", ["/api/v1/callback/result", "/api/v1/callback/event"])
def test_optional_business_data_may_be_omitted(path: str) -> None:
    payload = _result_payload() if path.endswith("result") else _event_payload()
    payload.pop("data")

    with _client() as client:
        response = client.post(path, json=payload)

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "ACK"}


@pytest.mark.parametrize(
    ("path", "payload", "headers"),
    [
        ("/api/v1/callback/result", _result_payload(), {}),
        ("/api/v1/callback/event", _event_payload(), {"content-type": "text/plain"}),
    ],
)
def test_callback_parses_valid_json_without_requiring_json_media_type(
    path: str,
    payload: dict[str, object],
    headers: dict[str, str],
) -> None:
    with _client() as client:
        response = client.post(
            path,
            content=json.dumps(payload),
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "ACK"}


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(json.dumps(_result_payload()).encode("utf-16"), id="utf-16"),
        pytest.param(json.dumps(_result_payload()).encode("utf-32"), id="utf-32"),
        pytest.param(b"\xff", id="invalid-utf-8"),
        pytest.param(
            json.dumps(_result_payload()).replace('"data": {}', '"data": {"value": NaN}').encode(),
            id="nan",
        ),
        pytest.param(
            json.dumps(_result_payload()).replace('"data": {}', '"data": {"value": Infinity}').encode(),
            id="infinity",
        ),
        pytest.param(
            json.dumps(_result_payload()).replace('"data": {}', '"data": {"value": -Infinity}').encode(),
            id="negative-infinity",
        ),
        pytest.param(
            json.dumps(_result_payload()).replace('"data": {}', '"data": {"value": 1e400}').encode(),
            id="positive-exponent-overflow",
        ),
        pytest.param(
            json.dumps(_result_payload()).replace('"data": {}', '"data": {"value": -1e400}').encode(),
            id="negative-exponent-overflow",
        ),
    ],
)
def test_callback_requires_utf8_standard_json(content: bytes) -> None:
    with _client() as client:
        response = client.post("/api/v1/callback/result", content=content)

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/callback/result", _result_payload()),
        ("/api/v1/callback/event", _event_payload()),
    ],
)
@pytest.mark.parametrize("location", ["top-level", "nested"])
def test_callback_rejects_duplicate_json_keys(path: str, payload: dict[str, object], location: str) -> None:
    content = json.dumps(payload)
    if location == "top-level":
        content = content.replace('"device_code": "ARM-01"', '"device_code": "ARM-01", "device_code": "OTHER"')
    else:
        content = content.replace('"data": {}', '"data": {"value": 1, "value": 2}')

    with _client() as client:
        response = client.post(path, content=content)

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/callback/result", _result_payload()),
        ("/api/v1/callback/event", _event_payload()),
    ],
)
@pytest.mark.parametrize("surrogate", [r"\ud800", r"\udc00"])
def test_callback_rejects_unpaired_unicode_surrogates(
    path: str,
    payload: dict[str, object],
    surrogate: str,
) -> None:
    content = json.dumps(payload).replace('"data": {}', f'"data": {{"value": "{surrogate}"}}')

    with _client() as client:
        response = client.post(path, content=content)

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/callback/result", _result_payload()),
        ("/api/v1/callback/event", _event_payload()),
    ],
)
def test_callback_rejects_excessive_json_nesting(path: str, payload: dict[str, object]) -> None:
    nested_data = '{"value":' * 10_000 + "0" + "}" * 10_000
    content = json.dumps(payload).replace('"data": {}', f'"data": {nested_data}')

    with _client(raise_server_exceptions=False) as client:
        response = client.post(path, content=content)

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


def test_invalid_envelope_does_not_echo_internal_metadata() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/callback/result",
            json={**_result_payload(), "trace_id": "TRACE-001"},
        )

    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


def test_body_limit_stops_streaming_before_buffering_remaining_chunks() -> None:
    class StreamingRequest:
        def __init__(self) -> None:
            self.chunks_read = 0
            self.headers = {"content-type": "application/json"}

        async def stream(self):
            for chunk in (b"x" * (256 * 1024), b"y", b"must-not-be-read"):
                self.chunks_read += 1
                yield chunk

    from src.app.device.v1.ecs_callback import EcsCallbackRejection, _decode_closed_body

    request = StreamingRequest()
    with pytest.raises(EcsCallbackRejection) as error:
        asyncio.run(_decode_closed_body(request, BaseModel))  # type: ignore[arg-type]

    assert error.value.status_code == 413
    assert request.chunks_read == 2
