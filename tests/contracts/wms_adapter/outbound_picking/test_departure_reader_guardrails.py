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
        "created_at": datetime(2026, 9, 14, 12),
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
        latest_for_workline=AsyncMock(return_value=confirmation),
        evidence=AsyncMock(return_value=evidence),
    )
    return RackDepartureResultReader(repository), repository


@pytest.mark.asyncio
async def test_workline_reader_ignores_departure_decided_before_current_arrival() -> None:
    reader, repository = _reader(_confirmation(created_at=datetime(2026, 9, 14, 11)), _evidence())

    assert await reader.latest_for_workline(object(), 7, "RACK-1", arrived_at=datetime(2026, 9, 14, 12)) is None
    repository.evidence.assert_not_awaited()


@pytest.mark.asyncio
async def test_workline_reader_keeps_decision_created_after_current_arrival() -> None:
    reader, _ = _reader(_confirmation(created_at=datetime(2026, 9, 14, 13)), _evidence())

    snapshot = await reader.latest_for_workline(object(), 7, "RACK-1", arrived_at=datetime(2026, 9, 14, 12))
    assert snapshot is not None
    assert snapshot.intent.operation_id == OPERATION_ID


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


@pytest.mark.asyncio
async def test_frozen_departure_read_does_not_use_latest_or_current_task_state():
    from src.utils.canonical_json import canonical_json_digest

    request = _request()
    request["data"]["task_id"] = None
    confirmation = _confirmation(
        request_payload=request,
        request_digest=canonical_json_digest(request),
        operation=OPERATION,
        workline_id=7,
        picking_task_id=None,
    )
    reader, repository = _reader(confirmation, _evidence(published_at=datetime(2026, 9, 14)))
    repository.by_operation_id = AsyncMock(return_value=confirmation)
    db = object()
    snapshot = await reader.by_operation_id(db, workline_id=7, rack_id="RACK-1", operation_id=OPERATION_ID)
    assert snapshot.intent.operation_id == OPERATION_ID and snapshot.evidence_id == 31
    repository.by_operation_id.assert_awaited_once_with(db, OPERATION_ID)
    repository.latest.assert_not_awaited()
    repository.latest_for_workline.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing", "workline", "owner", "operation", "operation_id", "digest", "rack", "unpublished"]
)
async def test_frozen_departure_read_rejects_other_owner_identity_or_unpublished_result(case):
    from src.utils.canonical_json import canonical_json_digest

    request = _request()
    request["data"]["task_id"] = None
    confirmation = _confirmation(
        request_payload=request,
        request_digest=canonical_json_digest(request),
        operation=OPERATION,
        workline_id=7,
        picking_task_id=None,
    )
    evidence = _evidence(published_at=datetime(2026, 9, 14))
    if case == "workline":
        confirmation.workline_id = 8
    elif case == "owner":
        confirmation.picking_task_id = 11
    elif case == "operation":
        confirmation.operation = "OTHER"
    elif case == "operation_id":
        confirmation.operation_id = "OTHER"
    elif case == "digest":
        confirmation.request_digest = "a" * 64
    elif case == "rack":
        request["data"]["rack_id"] = "OTHER"
        confirmation.request_digest = canonical_json_digest(request)
    elif case == "unpublished":
        evidence.published_at = None
    reader, repository = _reader(confirmation, evidence)
    repository.by_operation_id = AsyncMock(return_value=None if case == "missing" else confirmation)
    if case == "missing":
        assert (
            await reader.by_operation_id(object(), workline_id=7, rack_id="RACK-1", operation_id=OPERATION_ID) is None
        )
    else:
        with pytest.raises(ValueError):
            await reader.by_operation_id(object(), workline_id=7, rack_id="RACK-1", operation_id=OPERATION_ID)
    repository.latest.assert_not_awaited()
    repository.latest_for_workline.assert_not_awaited()
