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
    UnknownDeviceCommandError,
)
from src.app.device.v1.ecs_callback import router


@dataclass
class FakeEvidenceService:
    failure: Exception | None = None

    async def accept_result(self, result):
        if self.failure is not None:
            raise self.failure
        return DeviceEvidenceReceipt(1, f"RESULT:{result.command_code}", False, None)

    async def accept_event(self, event):
        if self.failure is not None:
            raise self.failure
        return DeviceEvidenceReceipt(2, "EVENT:derived", False, None)


def _client(service: FakeEvidenceService | None = None) -> TestClient:
    app = FastAPI()
    app.state.device_evidence_service = service or FakeEvidenceService()
    app.include_router(router, prefix="/api/v1/callback")
    return TestClient(app)


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
    with _client() as client:
        result = client.post("/api/v1/callback/result", json=_result_payload())
        event = client.post("/api/v1/callback/event", json=_event_payload())

    assert result.status_code == 200
    assert result.json() == {"code": 200, "message": "ACK"}
    assert event.status_code == 200
    assert event.json() == {"code": 200, "message": "ACK"}


def test_body_limit_is_checked_before_json_decode() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/callback/result",
            content=b"{" + b"x" * (256 * 1024),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["code"] == 413


def test_unknown_command_and_identity_conflict_are_explicit() -> None:
    with _client(FakeEvidenceService(UnknownDeviceCommandError("CMD-001"))) as client:
        missing = client.post("/api/v1/callback/result", json=_result_payload())
    with _client(FakeEvidenceService(DeviceEvidenceConflictError("RESULT-001"))) as client:
        conflict = client.post("/api/v1/callback/result", json=_result_payload())

    assert missing.status_code == 404
    assert missing.json()["message"] == "COMMAND_NOT_FOUND"
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "IDEMPOTENCY_CONFLICT"


def test_unavailable_callback_service_uses_closed_wire() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/callback")
    with TestClient(app) as client:
        unavailable = client.post("/api/v1/callback/result", json=_result_payload())

    assert unavailable.status_code == 503
    assert unavailable.json() == {"code": 503, "message": "TEMPORARILY_UNAVAILABLE"}


def test_closed_envelope_rejects_legacy_or_flattened_fields() -> None:
    payload = {**_result_payload(), "contract_key": "arm.pick"}
    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 400
    assert response.json()["message"] == "INVALID_ENVELOPE"


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
