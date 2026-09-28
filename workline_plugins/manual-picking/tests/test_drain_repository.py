from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.drain_repository import DRAIN_RACK_OUT_STEP, DrainRepository


def _record(result):
    return SimpleNamespace(
        workline_id=7,
        intent=sdk.ReturnBufferDrainIntent(
            operation_id="019f3406-2200-7b03-8b01-000000000003",
            workline_code="LINE-1",
            required_slot_count=2,
        ),
        result=result,
        evidence_id=9,
    )


@pytest.mark.asyncio
async def test_ready_reservation_closes_only_after_every_rack_departure_is_accepted() -> None:
    record = _record(
        sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)), sdk.RackFaceSequence("R2", ("270",))))
    )
    reader = SimpleNamespace(history=AsyncMock(return_value=(record,)))
    repository = DrainRepository(reader)
    repository.transport = AsyncMock(side_effect=[SimpleNamespace(status="ACCEPTED", result_deadline_at=None), None])

    assert await repository.current(object(), 7) is record
    repository.transport.side_effect = [
        SimpleNamespace(status="ACCEPTED", result_deadline_at=None),
        SimpleNamespace(status="SUCCEEDED", result_deadline_at=None),
    ]
    assert await repository.current(object(), 7) is None


def test_transport_identity_requires_workline_owner_and_exact_drain_rack() -> None:
    record = _record(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),)))
    binding = SimpleNamespace(
        workline_id=7,
        picking_task_id=None,
        step=DRAIN_RACK_OUT_STEP,
        correlation_id=f"drain:{record.intent.operation_id}:rack:R1",
        source_evidence_id=9,
        resource_fence_id="R1",
    )
    task = SimpleNamespace(authority_workline_id=7, kind="RACK_MOVE", request_json={"rack_id": "R1"})

    assert DrainRepository._validate_transport(record, "R1", binding.correlation_id, binding, task) is task
    binding.picking_task_id = 1
    with pytest.raises(ValueError):
        DrainRepository._validate_transport(record, "R1", binding.correlation_id, binding, task)


def test_departure_step_remains_explicit() -> None:
    assert DRAIN_RACK_OUT_STEP == "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"


def test_cancelled_departure_does_not_close_drain_reservation() -> None:
    cancelled = SimpleNamespace(status="FAILED", reason_code="RCS_TASK_CANCELLED", result_deadline_at=None)
    assert DrainRepository._accepted_or_terminal(cancelled) is False


@pytest.mark.asyncio
async def test_for_evidence_returns_none_when_no_operation_id_matches() -> None:
    reader = SimpleNamespace(operation_id_for_evidence=AsyncMock(return_value=None), history=AsyncMock())
    repository = DrainRepository(reader)

    assert await repository.for_evidence(object(), 7, 9) is None
    reader.history.assert_not_awaited()


@pytest.mark.asyncio
async def test_for_evidence_returns_the_checkpoint_record_when_history_succeeds() -> None:
    record = _record(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),)))
    reader = SimpleNamespace(
        operation_id_for_evidence=AsyncMock(return_value=record.intent.operation_id),
        history=AsyncMock(return_value=(record,)),
    )
    repository = DrainRepository(reader)
    db = object()

    assert await repository.for_evidence(db, 7, 9) is record
    reader.history.assert_awaited_once_with(db, workline_id=7, after_operation_id=record.intent.operation_id)


@pytest.mark.asyncio
async def test_for_evidence_propagates_history_integrity_violation() -> None:
    """history() 的 ValueError 必须保留为 evidence 的重试/对账信号。"""
    reader = SimpleNamespace(
        operation_id_for_evidence=AsyncMock(return_value="019f3406-2200-7b03-8b01-000000000003"),
        history=AsyncMock(side_effect=ValueError("drain history checkpoint Evidence missing or unpublished")),
    )
    repository = DrainRepository(reader)

    with pytest.raises(ValueError, match="checkpoint Evidence missing or unpublished"):
        await repository.for_evidence(object(), 7, 9)
