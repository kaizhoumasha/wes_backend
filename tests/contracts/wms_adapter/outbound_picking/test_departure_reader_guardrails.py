"""来源货架离场结果读取的 fail-closed 身份保护。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind, WmsConfirmationStatus
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader

OPERATION = "outbound.rack.departure_decide@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def _request(*, operation_id: str = OPERATION_ID, rack_id: str = "RACK-1") -> dict[str, object]:
    return {
        "operation": OPERATION,
        "operation_id": operation_id,
        "timestamp": 0,
        "data": {
            "task_id": "TASK-1",
            "rack_id": rack_id,
            "current_location": {"type": "RACK_POSITION", "location_code": "WORK-1"},
            "current_face": "A",
        },
    }


def _response(*, code: str = "DECIDED", result: str = "READY") -> dict[str, object]:
    data: dict[str, object]
    if result == "READY":
        data = {"result": "READY", "rack_destination": {"type": "ZONE", "location_code": "WH05"}}
    else:
        data = {"result": "WAIT", "retry_after_ms": 1000}
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}


def _confirmation(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "request_payload": _request(),
        "operation_id": OPERATION_ID,
        "status": WmsConfirmationStatus.COMPLETED,
        "response_evidence_id": 31,
        "response_result": "READY",
        "completed_at": datetime(2026, 9, 14, 12),
    }
    return SimpleNamespace(**(values | overrides))


def _evidence(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "id": 31,
        "kind": InboundEvidenceKind.WMS_RESULT,
        "apply_status": InboundEvidenceApplyStatus.APPLIED,
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "normalized_payload": _response(),
    }
    return SimpleNamespace(**(values | overrides))


def _reader(confirmation, evidence=None):  # type: ignore[no-untyped-def]
    repository = SimpleNamespace(
        latest=AsyncMock(return_value=confirmation),
        evidence=AsyncMock(return_value=evidence),
    )
    return RackDepartureResultReader(repository), repository


@pytest.mark.asyncio
async def test_reader_returns_none_when_no_original_confirmation_exists() -> None:
    reader, repository = _reader(None)

    assert await reader.latest(object(), 11, "RACK-1") is None
    repository.evidence.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confirmation",
    [
        _confirmation(operation_id="019f3405-2200-7b01-8b01-000000000002"),
        _confirmation(request_payload=_request(rack_id="OTHER-RACK")),
    ],
)
async def test_reader_rejects_confirmation_request_identity_drift(confirmation) -> None:  # type: ignore[no-untyped-def]
    reader, repository = _reader(confirmation)

    with pytest.raises(ValueError, match="request identity"):
        await reader.latest(object(), 11, "RACK-1")
    repository.evidence.assert_not_awaited()


@pytest.mark.asyncio
async def test_reader_keeps_non_completed_confirmation_without_reading_result_evidence() -> None:
    reader, repository = _reader(_confirmation(status=WmsConfirmationStatus.PENDING))

    snapshot = await reader.latest(object(), 11, "RACK-1")

    assert snapshot is not None
    assert snapshot.status is WmsConfirmationStatus.PENDING
    assert snapshot.outcome is None
    repository.evidence.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        None,
        _evidence(id=32),
        _evidence(kind=InboundEvidenceKind.WMS_EVENT),
        _evidence(apply_status=InboundEvidenceApplyStatus.RECONCILING),
        _evidence(operation="outbound.other@v1"),
        _evidence(operation_id="019f3405-2200-7b01-8b01-000000000002"),
    ],
)
async def test_reader_rejects_result_evidence_identity_drift(evidence) -> None:  # type: ignore[no-untyped-def]
    reader, _ = _reader(_confirmation(), evidence)

    with pytest.raises(ValueError, match="evidence identity"):
        await reader.latest(object(), 11, "RACK-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        (_evidence(normalized_payload={}), "approved response code"),
        (_evidence(normalized_payload=_response(code="OTHER")), "approved response status"),
        (_evidence(normalized_payload=_response(result="WAIT")), "differs from confirmation"),
    ],
)
async def test_reader_rejects_unapproved_or_drifting_completed_result(evidence, message: str) -> None:  # type: ignore[no-untyped-def]
    reader, _ = _reader(_confirmation(), evidence)

    with pytest.raises(ValueError, match=message):
        await reader.latest(object(), 11, "RACK-1")
