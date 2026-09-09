from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from src.app.wms_adapter.outbound_picking.manual_bin_completed_event_handler import (
    ManualBinCompletedHandler,
    ManualBinCompletedPersistenceResult,
)

if TYPE_CHECKING:
    from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import ManualBinCompletedEvent

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


@dataclass
class _Recorder:
    result: ManualBinCompletedPersistenceResult

    def __post_init__(self) -> None:
        self.envelopes: list[ManualBinCompletedEvent] = []

    async def record(self, envelope, *, received_at):  # type: ignore[no-untyped-def]
        self.envelopes.append(envelope)
        return self.result


def _event() -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "operation": "outbound.manual_bin.work_completed@v1",
        "timestamp": 1_788_390_000_000,
        "data": {
            "task_id": "PICK-001",
            "bin_code": "BIN-001",
            "result": "NORMAL",
            "completed_at": 1_788_389_999_000,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "http_status"), [("RECEIVED", 202), ("DUPLICATE", 200)])
async def test_completed_handler_acks_only_persisted_evidence(code: str, http_status: int) -> None:
    recorder = _Recorder(ManualBinCompletedPersistenceResult(code=code, timestamp_ms=123))

    response = await ManualBinCompletedHandler(recorder).handle(_event())

    assert response.http_status == http_status
    assert response.body == {"operation_id": OPERATION_ID, "code": code, "timestamp": 123, "data": {}}
    assert recorder.envelopes[0].data.bin_code == "BIN-001"


@pytest.mark.asyncio
async def test_completed_handler_rejects_invalid_data_without_business_lookup() -> None:
    recorder = _Recorder(
        ManualBinCompletedPersistenceResult(code="REJECTED", timestamp_ms=123, reason_code="INVALID_DATA")
    )
    event = _event()
    event["data"] = {**event["data"], "result": "UNKNOWN"}  # type: ignore[arg-type]

    response = await ManualBinCompletedHandler(recorder).handle(event)

    assert response.http_status == 422
    assert response.body["data"] == {"reason_code": "INVALID_DATA"}
