from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_event_handler import (
    ManualRackDirectPickHandler,
    ManualRackDirectPickPersistenceResult,
)

if TYPE_CHECKING:
    from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_wire import ManualRackDirectPickEvent

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


@dataclass
class _Recorder:
    result: ManualRackDirectPickPersistenceResult

    def __post_init__(self) -> None:
        self.envelopes: list[ManualRackDirectPickEvent] = []

    async def record(self, envelope, *, received_at):  # type: ignore[no-untyped-def]
        self.envelopes.append(envelope)
        return self.result


def _event() -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "operation": "outbound.manual_rack.direct_pick_completed@v1",
        "timestamp": 1_788_390_000_000,
        "data": {
            "task_id": "PICK-001",
            "plan_revision": 1,
            "rack_id": "RACK-001",
            "rack_face": "A1",
            "completed_at": 1_788_389_999_000,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "http_status"), [("RECEIVED", 202), ("DUPLICATE", 200)])
async def test_handler_acks_only_persisted_evidence(code: str, http_status: int) -> None:
    recorder = _Recorder(ManualRackDirectPickPersistenceResult(code=code, timestamp_ms=123))

    response = await ManualRackDirectPickHandler(recorder).handle(_event())

    assert response.http_status == http_status
    assert response.body == {"operation_id": OPERATION_ID, "code": code, "timestamp": 123, "data": {}}
    assert recorder.envelopes[0].data.rack_id == "RACK-001"


@pytest.mark.asyncio
async def test_handler_rejects_invalid_data_without_business_lookup() -> None:
    recorder = _Recorder(
        ManualRackDirectPickPersistenceResult(code="REJECTED", timestamp_ms=123, reason_code="INVALID_DATA")
    )
    event = _event()
    event["data"] = {**event["data"], "completed_at": 9_999_999_999_999}  # type: ignore[arg-type]

    response = await ManualRackDirectPickHandler(recorder).handle(event)

    assert response.http_status == 422
    assert response.body["data"] == {"reason_code": "INVALID_DATA"}


@pytest.mark.asyncio
async def test_handler_returns_400_for_invalid_operation_id_format() -> None:
    event = _event()
    event["operation_id"] = "invalid-id"  # Not a valid UUID7

    response = await ManualRackDirectPickHandler(
        _Recorder(ManualRackDirectPickPersistenceResult(code="RECEIVED", timestamp_ms=123))
    ).handle(event)

    assert response.http_status == 400
    assert response.body == {}


@pytest.mark.asyncio
async def test_handler_returns_400_for_missing_operation_id() -> None:
    event = _event()
    del event["operation_id"]  # type: ignore[attr-defined]

    response = await ManualRackDirectPickHandler(
        _Recorder(ManualRackDirectPickPersistenceResult(code="RECEIVED", timestamp_ms=123))
    ).handle(event)

    assert response.http_status == 400
    assert response.body == {}


@pytest.mark.asyncio
async def test_handler_returns_400_for_invalid_operation_format() -> None:
    event = _event()
    event["operation"] = ""  # Empty operation

    response = await ManualRackDirectPickHandler(
        _Recorder(ManualRackDirectPickPersistenceResult(code="RECEIVED", timestamp_ms=123))
    ).handle(event)

    assert response.http_status == 400
    assert response.body == {}


@pytest.mark.asyncio
async def test_handler_returns_400_for_missing_operation() -> None:
    event = _event()
    del event["operation"]  # type: ignore[attr-defined]

    response = await ManualRackDirectPickHandler(
        _Recorder(ManualRackDirectPickPersistenceResult(code="RECEIVED", timestamp_ms=123))
    ).handle(event)

    assert response.http_status == 400
    assert response.body == {}


@pytest.mark.asyncio
async def test_handler_returns_422_for_wrong_operation() -> None:
    event = _event()
    event["operation"] = "outbound.manual_bin.work_completed@v1"  # Wrong operation

    response = await ManualRackDirectPickHandler(
        _Recorder(ManualRackDirectPickPersistenceResult(code="RECEIVED", timestamp_ms=123))
    ).handle(event)

    assert response.http_status == 422
    assert response.body["code"] == "REJECTED"
    assert response.body["data"] == {"reason_code": "UNSUPPORTED_OPERATION"}


@pytest.mark.asyncio
async def test_handler_returns_503_when_recorder_raises() -> None:
    class FailingRecorder:
        async def record(self, envelope, *, received_at):  # type: ignore[no-untyped-def]
            raise RuntimeError("Database connection failed")

    response = await ManualRackDirectPickHandler(FailingRecorder()).handle(_event())

    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_handler_returns_503_for_unknown_persistence_result_code() -> None:
    recorder = _Recorder(ManualRackDirectPickPersistenceResult(code="UNKNOWN_CODE", timestamp_ms=123))

    response = await ManualRackDirectPickHandler(recorder).handle(_event())

    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"
