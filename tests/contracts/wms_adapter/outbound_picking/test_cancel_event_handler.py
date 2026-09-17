from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from src.app.wms_adapter.outbound_picking.cancel_event_handler import (
    PickingTaskCancelHandler,
    PickingTaskCancelPersistenceResult,
)

if TYPE_CHECKING:
    from datetime import datetime


def _task_body() -> bytes:
    return json.dumps(
        {
            "operation_id": "019f3410-0000-7000-8000-000000000001",
            "operation": "outbound.picking_task.cancel@v1",
            "timestamp": 1786065200000,
            "data": {
                "task_id": "PICK-20260811-001",
                "cancel_scope": "TASK",
            },
        }
    ).encode()


def _plan_members_body() -> bytes:
    return json.dumps(
        {
            "operation_id": "019f3410-0000-7000-8000-000000000002",
            "operation": "outbound.picking_task.cancel@v1",
            "timestamp": 1786065201000,
            "data": {
                "task_id": "PICK-20260811-001",
                "cancel_scope": "PLAN_MEMBERS",
                "bin_source_racks": [{"rack_id": "RACK-5F-001", "rack_faces": ["90", "270"]}],
            },
        }
    ).encode()


def _invalid_body() -> bytes:
    """PLAN_MEMBERS 缺失全部选择器；触发 validate_selectors 拒绝。"""
    return json.dumps(
        {
            "operation_id": "019f3410-0000-7000-8000-000000000003",
            "operation": "outbound.picking_task.cancel@v1",
            "timestamp": 1786065202000,
            "data": {
                "task_id": "PICK-20260811-001",
                "cancel_scope": "PLAN_MEMBERS",
            },
        }
    ).encode()


@dataclass
class _Recorder:
    result: PickingTaskCancelPersistenceResult
    envelope: object | None = None
    received_at: datetime | None = None

    async def record(self, envelope: object, *, received_at: datetime) -> PickingTaskCancelPersistenceResult:
        self.envelope = envelope
        self.received_at = received_at
        return self.result


class _FailingRecorder:
    async def record(self, envelope: object, *, received_at: datetime) -> PickingTaskCancelPersistenceResult:
        raise RuntimeError("database unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "result", "expected_status", "expected_code", "expected_data"),
    [
        (_task_body(), PickingTaskCancelPersistenceResult("RECEIVED", 1786065200123), 202, "RECEIVED", {}),
        (_task_body(), PickingTaskCancelPersistenceResult("DUPLICATE", 1786065200123), 200, "DUPLICATE", {}),
        (
            _plan_members_body(),
            PickingTaskCancelPersistenceResult("CONFLICT", 1786065200123, "REFERENCE_CONFLICT"),
            409,
            "CONFLICT",
            {"reason_code": "REFERENCE_CONFLICT"},
        ),
        (
            _plan_members_body(),
            PickingTaskCancelPersistenceResult("CONFLICT", 1786065200123, "STATE_CONFLICT"),
            409,
            "CONFLICT",
            {"reason_code": "STATE_CONFLICT"},
        ),
        (
            _task_body(),
            PickingTaskCancelPersistenceResult("CONFLICT", 1786065200123, "IDEMPOTENCY_CONFLICT"),
            409,
            "CONFLICT",
            {"reason_code": "IDEMPOTENCY_CONFLICT"},
        ),
    ],
)
async def test_handler_maps_persisted_outcomes_to_the_approved_ack(
    body: bytes,
    result: PickingTaskCancelPersistenceResult,
    expected_status: int,
    expected_code: str,
    expected_data: dict[str, str],
) -> None:
    recorder = _Recorder(result)
    handler = PickingTaskCancelHandler(recorder)

    response = await handler.handle(json.loads(body))

    assert response.http_status == expected_status
    assert response.body["code"] == expected_code
    assert response.body["data"] == expected_data
    assert recorder.envelope is not None
    assert recorder.received_at is not None


@pytest.mark.asyncio
async def test_handler_returns_invalid_data_only_after_recorder_accepts_rejection() -> None:
    recorder = _Recorder(PickingTaskCancelPersistenceResult("REJECTED", 1786065200123, "INVALID_DATA"))
    handler = PickingTaskCancelHandler(recorder)

    response = await handler.handle(json.loads(_invalid_body()))

    assert response.http_status == 422
    assert response.body["code"] == "REJECTED"
    assert response.body["data"] == {"reason_code": "INVALID_DATA"}
    assert recorder.envelope is not None
    assert recorder.envelope.raw_envelope == json.loads(_invalid_body())


@pytest.mark.asyncio
async def test_invalid_data_returns_unavailable_when_rejection_cannot_commit() -> None:
    response = await PickingTaskCancelHandler(_FailingRecorder()).handle(json.loads(_invalid_body()))
    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_handler_rejects_another_well_formed_operation() -> None:
    payload = json.loads(_task_body())
    payload["operation"] = "outbound.picking_task.queue_changed@v1"
    handler = PickingTaskCancelHandler(_Recorder(PickingTaskCancelPersistenceResult("RECEIVED", 1786065200123)))

    response = await handler.handle(payload)

    assert response.http_status == 422
    assert response.body["code"] == "REJECTED"
    assert response.body["data"] == {"reason_code": "UNSUPPORTED_OPERATION"}


@pytest.mark.asyncio
async def test_handler_returns_unavailable_when_persistence_did_not_accept_the_message() -> None:
    handler = PickingTaskCancelHandler(_FailingRecorder())

    response = await handler.handle(json.loads(_task_body()))

    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"
    assert response.body["data"] == {}


@pytest.mark.asyncio
async def test_handler_rejects_malformed_operation_id_before_recording() -> None:
    payload = json.loads(_task_body())
    payload["operation_id"] = "not-a-uuid7"
    handler = PickingTaskCancelHandler(_Recorder(PickingTaskCancelPersistenceResult("RECEIVED", 1786065200123)))

    response = await handler.handle(payload)

    assert response.http_status == 400
    assert response.body == {}
