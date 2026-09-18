"""退料货架到位报告可靠调度器与结果读取的 fail-closed 身份保护。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import TransportRackPosition, wms_operations

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind, WmsConfirmationStatus
from src.app.wms_integration.outbound_picking.repositories.return_rack_arrival_repository import (
    ReturnRackArrivalRepository,
)
from src.app.wms_integration.outbound_picking.services.return_rack_arrival import (
    ReturnRackArrivalResultReader,
    ReturnRackArrivalScheduler,
)

OPERATION = "outbound.return_rack.arrival_report@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def _request(*, operation_id: str = OPERATION_ID, rack_id: str = "RACK-1") -> dict[str, object]:
    return {
        "operation": OPERATION,
        "operation_id": operation_id,
        "timestamp": 0,
        "data": {
            "task_id": "TASK-1",
            "transport_task_id": "TT-1",
            "outcome_revision": 1,
            "rack_id": rack_id,
            "final_position": {"type": "RACK_POSITION", "location_code": "WORK-1"},
            "arrival_face": "A",
        },
    }


def _response(*, code: str = "RECORDED") -> dict[str, object]:
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": {}}


def _confirmation(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "request_payload": _request(),
        "operation_id": OPERATION_ID,
        "status": WmsConfirmationStatus.COMPLETED,
        "response_evidence_id": 31,
        "response_result": "RECORDED",
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
    return ReturnRackArrivalResultReader(repository), repository


def test_repository_has_no_workline_owner_query() -> None:
    """到位报告的 request data 恒有 task_id，恒为 task-owned，因此不提供 latest_for_workline。"""
    assert not hasattr(ReturnRackArrivalRepository, "latest_for_workline")


def test_repository_latest_filters_by_task_rack_and_arrival_operation() -> None:
    repository = ReturnRackArrivalRepository()
    captured: dict[str, object] = {}

    async def fake_scalar(query):  # type: ignore[no-untyped-def]
        captured["sql"] = str(query.compile(compile_kwargs={"literal_binds": True}))

    db = SimpleNamespace(scalar=fake_scalar)

    import asyncio

    asyncio.run(repository.latest(db, 11, "RACK-1"))

    sql = captured["sql"]
    assert "picking_task_id" in sql
    assert OPERATION in sql
    assert "RACK-1" in sql


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
    assert snapshot.intent.rack_id == "RACK-1"
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
        (_evidence(normalized_payload=_response(code="DUPLICATE")), "differs from confirmation"),
    ],
)
async def test_reader_rejects_unapproved_or_drifting_completed_result(evidence, message: str) -> None:  # type: ignore[no-untyped-def]
    reader, _ = _reader(_confirmation(), evidence)

    with pytest.raises(ValueError, match=message):
        await reader.latest(object(), 11, "RACK-1")


@pytest.mark.asyncio
async def test_reader_decodes_recorded_outcome_and_evidence_id() -> None:
    confirmation = _confirmation()
    evidence = _evidence()
    reader, _ = _reader(confirmation, evidence)

    snapshot = await reader.latest(object(), 11, "RACK-1")

    assert snapshot is not None
    assert snapshot.outcome is not None
    assert snapshot.outcome.result.duplicate is False
    assert snapshot.evidence_id == 31
    assert snapshot.completed_at == datetime(2026, 9, 14, 12)


@pytest.mark.asyncio
async def test_scheduler_requires_picking_task_id_and_freezes_identity() -> None:
    intent = wms_operations.outbound_return_rack_arrival_report(
        operation_id=OPERATION_ID,
        task_id="TASK-1",
        transport_task_id="TT-1",
        outcome_revision=1,
        rack_id="RACK-1",
        final_position=TransportRackPosition("WORK-1"),
        arrival_face="A",
    )
    confirmations = SimpleNamespace(create_or_get=AsyncMock(return_value=SimpleNamespace(duplicate=False)))
    await ReturnRackArrivalScheduler(confirmations).create_in_session(
        object(), intent, picking_task_id=11, created_at=datetime(2026, 9, 14, 12)
    )
    kwargs = confirmations.create_or_get.await_args.kwargs
    assert kwargs["operation"] == OPERATION
    assert kwargs["operation_id"] == OPERATION_ID
    assert kwargs["picking_task_id"] == 11
    assert kwargs["request_payload"]["data"] == _request()["data"]
