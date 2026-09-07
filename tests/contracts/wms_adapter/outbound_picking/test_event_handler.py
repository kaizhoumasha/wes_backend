from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from src.app.wms_adapter.outbound_picking.event_handler import (
    PickingTaskIssuedHandler,
    PickingTaskIssuedPersistenceResult,
)

if TYPE_CHECKING:
    from datetime import datetime


def _body(*, queue_revision: int = 1) -> bytes:
    return json.dumps(
        {
            "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
            "operation": "outbound.picking_task.issued@v1",
            "timestamp": 1786060800000,
            "data": {
                "task_id": "PICK-20260811-001",
                "task_type": "MANUAL",
                "queue_revision": queue_revision,
                "dispatch_sequence": 100,
            },
        }
    ).encode()


@dataclass
class _Recorder:
    result: PickingTaskIssuedPersistenceResult
    envelope: object | None = None
    received_at: datetime | None = None

    async def record(self, envelope: object, *, received_at: datetime) -> PickingTaskIssuedPersistenceResult:
        self.envelope = envelope
        self.received_at = received_at
        return self.result


class _FailingRecorder:
    async def record(self, envelope: object, *, received_at: datetime) -> PickingTaskIssuedPersistenceResult:
        raise RuntimeError("database unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status", "expected_code", "expected_data"),
    [
        (PickingTaskIssuedPersistenceResult("RECEIVED", 1786060800123), 202, "RECEIVED", {}),
        (PickingTaskIssuedPersistenceResult("DUPLICATE", 1786060800123), 200, "DUPLICATE", {}),
        (
            PickingTaskIssuedPersistenceResult("CONFLICT", 1786060800123, "IDEMPOTENCY_CONFLICT"),
            409,
            "CONFLICT",
            {"reason_code": "IDEMPOTENCY_CONFLICT"},
        ),
        (
            PickingTaskIssuedPersistenceResult("CONFLICT", 1786060800123, "STATE_CONFLICT"),
            409,
            "CONFLICT",
            {"reason_code": "STATE_CONFLICT"},
        ),
    ],
)
async def test_handler_maps_persisted_outcomes_to_the_approved_ack(
    result: PickingTaskIssuedPersistenceResult,
    expected_status: int,
    expected_code: str,
    expected_data: dict[str, str],
) -> None:
    recorder = _Recorder(result)
    handler = PickingTaskIssuedHandler(recorder)

    response = await handler.handle(json.loads(_body()))

    assert response.http_status == expected_status
    assert response.body == {
        "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
        "code": expected_code,
        "timestamp": 1786060800123,
        "data": expected_data,
    }
    assert recorder.envelope is not None
    assert recorder.received_at is not None


@pytest.mark.asyncio
async def test_handler_returns_invalid_data_only_after_recorder_accepts_rejection() -> None:
    recorder = _Recorder(PickingTaskIssuedPersistenceResult("REJECTED", 1786060800123, "INVALID_DATA"))
    handler = PickingTaskIssuedHandler(recorder)

    response = await handler.handle(json.loads(_body(queue_revision=2)))

    assert response.http_status == 422
    assert response.body["code"] == "REJECTED"
    assert response.body["data"] == {"reason_code": "INVALID_DATA"}
    assert recorder.envelope is not None
    assert recorder.envelope.raw_envelope == json.loads(_body(queue_revision=2))
    assert response.body["timestamp"] == 1786060800123


@pytest.mark.asyncio
async def test_invalid_data_returns_unavailable_when_rejection_cannot_commit() -> None:
    response = await PickingTaskIssuedHandler(_FailingRecorder()).handle(json.loads(_body(queue_revision=2)))
    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_handler_rejects_another_well_formed_operation() -> None:
    payload = json.loads(_body())
    payload["operation"] = "outbound.picking_task.queue_changed@v1"
    handler = PickingTaskIssuedHandler(_Recorder(PickingTaskIssuedPersistenceResult("RECEIVED", 1786060800123)))

    response = await handler.handle(payload)

    assert response.http_status == 422
    assert response.body["code"] == "REJECTED"
    assert response.body["data"] == {"reason_code": "UNSUPPORTED_OPERATION"}


@pytest.mark.asyncio
async def test_handler_returns_unavailable_when_persistence_did_not_accept_the_message() -> None:
    handler = PickingTaskIssuedHandler(_FailingRecorder())

    response = await handler.handle(json.loads(_body()))

    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"
    assert response.body["data"] == {}
