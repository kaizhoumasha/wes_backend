"""统一 ECS callback API facade。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

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
from src.app.device.v1 import ecs_callback as ecs_callback_module
from src.app.device.v1.ecs_callback import router


@dataclass
class FakeEvidenceService:
    failure: Exception | None = None
    duplicate: bool = False
    accepted: list[BaseModel] = field(default_factory=list)

    async def accept_result(self, result):
        if self.failure is not None:
            raise self.failure
        self.accepted.append(result)
        return DeviceEvidenceReceipt(1, f"RESULT:{result.command_code}", self.duplicate, None, "PENDING")

    async def accept_event(self, event):
        if self.failure is not None:
            raise self.failure
        self.accepted.append(event)
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


def _invalid_envelope(*issues: dict[str, str]) -> dict[str, object]:
    return {
        "code": 400,
        "message": "INVALID_ENVELOPE",
        "error_detail": {"issues": list(issues)},
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


def test_debug_event_passes_strict_flag_to_service() -> None:
    service = FakeEvidenceService()
    payload = {**_event_payload(), "is_debug": True}

    with _client(service) as client:
        response = client.post("/api/v1/callback/event", json=payload)

    assert response.status_code == 200
    assert service.accepted[0].model_dump(mode="json") == payload


@pytest.mark.parametrize("invalid_flag", [None, "true", 1])
def test_debug_event_rejects_non_boolean_flag(invalid_flag: object) -> None:
    with _client() as client:
        response = client.post("/api/v1/callback/event", json={**_event_payload(), "is_debug": invalid_flag})

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "is_debug", "code": "INVALID_TYPE", "expected": "boolean"})


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


@pytest.mark.parametrize(
    ("path", "payload", "expected_payload"),
    [
        ("/api/v1/callback/result", {**_result_payload(), "actual_qty": 1}, _result_payload()),
        (
            "/api/v1/callback/event",
            {**_event_payload(), "supplier_extension": {"value": 1}},
            {**_event_payload(), "is_debug": False},
        ),
    ],
)
def test_callback_accepts_and_ignores_top_level_supplier_extensions(
    path: str, payload: dict[str, object], expected_payload: dict[str, object]
) -> None:
    service = FakeEvidenceService()
    with _client(service) as client:
        response = client.post(path, json=payload)

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "ACK"}
    accepted_payload = service.accepted[0].model_dump(mode="json")
    assert accepted_payload == expected_payload


def test_callback_error_detail_remains_closed() -> None:
    payload = {
        **_result_payload(),
        "result": "FAILED",
        "error_detail": {"code": "TARGET_BLOCKED", "msg": "Path blocked", "supplier_detail": "opaque"},
    }

    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "error_detail.<extra>", "code": "EXTRA_FORBIDDEN"})


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
    assert response.json() == _invalid_envelope({"field": field, "code": "INVALID_TYPE", "expected": "integer"})


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/callback/result", _result_payload()),
        ("/api/v1/callback/event", _event_payload()),
    ],
)
def test_callback_rejection_explains_required_string_fields(path: str, payload: dict[str, object]) -> None:
    with _client() as client:
        response = client.post(path, json={**payload, "device_code": None})

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "device_code", "code": "INVALID_TYPE", "expected": "string"})


@pytest.mark.parametrize(
    ("path", "payload", "required_field"),
    [
        ("/api/v1/callback/result", _result_payload(), "result"),
        ("/api/v1/callback/event", _event_payload(), "event_type"),
    ],
)
def test_callback_rejection_explains_missing_required_fields(
    path: str, payload: dict[str, object], required_field: str
) -> None:
    del payload[required_field]
    with _client() as client:
        response = client.post(path, json=payload)

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": required_field, "code": "FIELD_REQUIRED"})


def test_result_rejection_explains_invalid_result_value() -> None:
    with _client() as client:
        response = client.post("/api/v1/callback/result", json={**_result_payload(), "result": "UNKNOWN"})

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "result", "code": "INVALID_VALUE"})


def test_result_rejection_explains_nullable_data_mismatch() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/callback/result",
            json={**_result_payload(), "data": None},
        )

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "data", "code": "INVALID_TYPE", "expected": "object"})


def test_result_rejection_explains_model_level_invalid_value() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/callback/result",
            json={
                **_result_payload(),
                "error_detail": {"code": "UNEXPECTED", "msg": "Successful result cannot include an error"},
            },
        )

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "$", "code": "INVALID_VALUE"})


def test_result_rejection_explains_supplier_private_error_detail_fields() -> None:
    payload = {
        **_result_payload(),
        "result": "FAILED",
        "error_detail": {"error_code": "TARGET_BLOCKED", "error_message": "Path blocked"},
    }
    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json() == _invalid_envelope(
        {"field": "error_detail.code", "code": "FIELD_REQUIRED"},
        {"field": "error_detail.msg", "code": "FIELD_REQUIRED"},
        {"field": "error_detail.<extra>", "code": "EXTRA_FORBIDDEN"},
        {"field": "error_detail.<extra>", "code": "EXTRA_FORBIDDEN"},
    )


def test_failed_result_rejection_explains_required_error_detail() -> None:
    payload = {**_result_payload(), "result": "FAILED", "error_detail": None}
    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "error_detail", "code": "FIELD_REQUIRED"})


@pytest.mark.parametrize("error_detail", ["not-an-object", []], ids=["string", "array"])
def test_failed_result_rejection_explains_error_detail_type(error_detail: object) -> None:
    payload = {**_result_payload(), "result": "FAILED", "error_detail": error_detail}
    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "error_detail", "code": "INVALID_TYPE", "expected": "object"})


def test_invalid_envelope_logs_only_field_and_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(ecs_callback_module.logger, "warning", messages.append)

    with _client() as client:
        response = client.post(
            "/api/v1/callback/result",
            json={**_result_payload(), "finish_time": "DO-NOT-LOG"},
        )

    assert response.status_code == 400
    assert messages == ["device.ingress.invalid_envelope model=EcsCommandResultReport issues=finish_time:INVALID_TYPE"]


@pytest.mark.parametrize(
    ("content", "issue"),
    [
        (b'{"secret":"do-not-log"', "$:INVALID_JSON"),
        (b'"do-not-log"', "$:INVALID_TYPE"),
    ],
    ids=["invalid-json", "non-object-root"],
)
def test_non_model_invalid_envelope_logs_only_sanitized_issue(
    monkeypatch: pytest.MonkeyPatch, content: bytes, issue: str
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(ecs_callback_module.logger, "warning", messages.append)

    with _client() as client:
        response = client.post("/api/v1/callback/result", content=content)

    assert response.status_code == 400
    assert messages == [f"device.ingress.invalid_envelope model=EcsCommandResultReport issues={issue}"]
    assert all("do-not-log" not in message for message in messages)


def test_callback_redacts_request_controlled_error_detail_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []
    malicious_key = "supplier_extra\nforged=1 secret=do-not-log"
    payload = {
        **_result_payload(),
        "result": "FAILED",
        "error_detail": {
            "code": "TARGET_BLOCKED",
            "msg": "Path blocked",
            malicious_key: "opaque",
        },
    }
    monkeypatch.setattr(ecs_callback_module.logger, "warning", messages.append)

    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "error_detail.<extra>", "code": "EXTRA_FORBIDDEN"})
    assert messages == [
        "device.ingress.invalid_envelope model=EcsCommandResultReport issues=error_detail.<extra>:EXTRA_FORBIDDEN"
    ]
    assert malicious_key not in str(response.json())
    assert all(malicious_key not in message for message in messages)


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


@pytest.mark.parametrize("path", ["/api/v1/callback/result", "/api/v1/callback/event"])
@pytest.mark.parametrize("payload", [[], "not-an-envelope", 1, None], ids=["array", "string", "number", "null"])
def test_callback_rejects_non_object_json_roots(path: str, payload: object) -> None:
    with _client() as client:
        response = client.post(path, content=json.dumps(payload))

    assert response.status_code == 400
    assert response.json() == _invalid_envelope({"field": "$", "code": "INVALID_TYPE", "expected": "object"})


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
    assert response.json() == _invalid_envelope({"field": "$", "code": "INVALID_JSON"})


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
    assert response.json() == _invalid_envelope({"field": "$", "code": "INVALID_JSON"})


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
    assert response.json() == _invalid_envelope({"field": "$", "code": "INVALID_JSON"})


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
    assert response.json() == _invalid_envelope({"field": "$", "code": "INVALID_JSON"})


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
