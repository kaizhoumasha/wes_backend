from dataclasses import dataclass, field
from typing import Any

import pytest

from src.app.wms_adapter.outbound_picking.plan_delta_event_handler import (
    PickingTaskPlanDeltaHandler,
    PickingTaskPlanDeltaPersistenceResult,
)
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PickingTaskPlanDeltaInvalidData
from tests.contracts.wms_adapter.outbound_picking.test_plan_delta_wire import valid_event


@dataclass
class Recorder:
    result: Any
    receipts: list = field(default_factory=list)

    async def record(self, envelope, *, received_at):
        self.receipts.append((envelope, received_at))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    ("code", "reason", "status"),
    [
        ("RECEIVED", None, 202),
        ("DUPLICATE", None, 200),
        ("UNAVAILABLE", None, 503),
        ("REJECTED", "INVALID_DATA", 422),
        ("CONFLICT", "STATE_CONFLICT", 409),
        ("CONFLICT", "REVISION_CONFLICT", 409),
        ("CONFLICT", "REFERENCE_CONFLICT", 409),
        ("CONFLICT", "IDEMPOTENCY_CONFLICT", 409),
    ],
)
async def test_persisted_receipt_maps_closed_ack(code, reason, status):
    recorder = Recorder(PickingTaskPlanDeltaPersistenceResult(code, 1786060801234, reason))
    result = await PickingTaskPlanDeltaHandler(recorder).handle(valid_event())
    assert result.http_status == status
    assert result.body == {
        "operation_id": valid_event()["operation_id"],
        "code": code,
        "timestamp": 1786060801234,
        "data": {"reason_code": reason} if reason else {},
    }
    assert len(recorder.receipts) == 1


async def test_invalid_data_goes_to_recorder_before_rejection():
    recorder = Recorder(PickingTaskPlanDeltaPersistenceResult("REJECTED", 1786060801234, "INVALID_DATA"))
    payload = valid_event()
    payload["data"]["target_rack"] = None
    response = await PickingTaskPlanDeltaHandler(recorder).handle(payload)
    assert response.http_status == 422
    assert isinstance(recorder.receipts[0][0], PickingTaskPlanDeltaInvalidData)
    assert recorder.receipts[0][0].raw_envelope == payload


@pytest.mark.parametrize(
    "persisted",
    [
        RuntimeError("storage failed"),
        PickingTaskPlanDeltaPersistenceResult("CONFLICT", 1, "UNAPPROVED"),
        PickingTaskPlanDeltaPersistenceResult("REJECTED", 1, "STATE_CONFLICT"),
    ],
)
async def test_failed_or_unknown_persistence_result_fails_closed(persisted):
    recorder = Recorder(persisted)
    response = await PickingTaskPlanDeltaHandler(recorder).handle(valid_event())
    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"
    assert response.body["data"] == {}
