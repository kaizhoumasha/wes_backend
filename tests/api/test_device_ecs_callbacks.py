"""统一 ECS callback API facade。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
        return DeviceEvidenceReceipt(1, result.source_event_id, False, result.trace_id)

    async def accept_event(self, event):
        if self.failure is not None:
            raise self.failure
        return DeviceEvidenceReceipt(2, event.source_event_id, False, event.trace_id)


def _client(service: FakeEvidenceService | None = None) -> TestClient:
    app = FastAPI()
    app.state.device_evidence_service = service or FakeEvidenceService()
    app.include_router(router, prefix="/api/v1/callback")
    return TestClient(app)


def _result_payload() -> dict[str, object]:
    return {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "result": "SUCCESS",
        "finish_time": 1_786_579_204_000,
        "source_event_id": "RESULT-001",
        "data": {},
        "error_detail": None,
        "trace_id": "TRACE-001",
    }


def _event_payload() -> dict[str, object]:
    return {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "event_type": "DEVICE_CONTRACT_EVENT",
        "timestamp": 1_786_579_204_000,
        "source_event_id": "EVENT-001",
        "data": {},
        "trace_id": "TRACE-002",
    }


def test_result_and_event_ack_only_after_service_returns() -> None:
    with _client() as client:
        result = client.post("/api/v1/callback/result", json=_result_payload())
        event = client.post("/api/v1/callback/event", json=_event_payload())

    assert result.status_code == 200
    assert result.json() == {"code": 200, "message": "ACK", "trace_id": "TRACE-001"}
    assert event.status_code == 200
    assert event.json() == {"code": 200, "message": "ACK", "trace_id": "TRACE-002"}


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


def test_closed_envelope_rejects_legacy_or_flattened_fields() -> None:
    payload = {**_result_payload(), "device_id": 7}
    with _client() as client:
        response = client.post("/api/v1/callback/result", json=payload)

    assert response.status_code == 422
