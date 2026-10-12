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
async def test_ready_execution_remains_current_until_driver_closes_it() -> None:
    record = _record(
        sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)), sdk.RackFaceSequence("R2", ("270",))))
    )
    reader = SimpleNamespace(
        for_operation=AsyncMock(return_value=record), pending_for_workline=AsyncMock(return_value=None)
    )
    execution = SimpleNamespace(drain_operation_id=record.intent.operation_id, decision_evidence_id=9)
    executions = SimpleNamespace(active_for_workline=AsyncMock(return_value=execution))
    repository = DrainRepository(reader, executions=executions)
    repository.transport = AsyncMock(side_effect=[SimpleNamespace(status="ACCEPTED", result_deadline_at=None), None])

    assert await repository.current(object(), 7) is record
    repository.transport.side_effect = [
        SimpleNamespace(status="ACCEPTED", result_deadline_at=None),
        SimpleNamespace(status="SUCCEEDED", result_deadline_at=None),
    ]
    assert await repository.current(object(), 7) is record


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
    reader = SimpleNamespace(
        operation_id_for_evidence=AsyncMock(return_value=None), for_operation=AsyncMock(), history=AsyncMock()
    )
    executions = SimpleNamespace(for_evidence=AsyncMock(return_value=None))
    repository = DrainRepository(reader, executions=executions)

    assert await repository.for_evidence(object(), 7, 9) is None
    executions.for_evidence.assert_awaited_once()
    reader.history.assert_not_awaited()
    reader.for_operation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED", "EXCEPTION_RETIRED"])
async def test_for_evidence_returns_the_checkpoint_record_when_history_succeeds(status) -> None:
    record = _record(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),)))
    reader = SimpleNamespace(
        operation_id_for_evidence=AsyncMock(return_value=record.intent.operation_id),
        for_operation=AsyncMock(return_value=record),
    )
    execution = SimpleNamespace(status=status, drain_operation_id=record.intent.operation_id, closed_at=object())
    executions = SimpleNamespace(for_evidence=AsyncMock(return_value=execution))
    repository = DrainRepository(reader, executions=executions)
    db = object()

    assert await repository.for_evidence(db, 7, 9) is record
    executions.for_evidence.assert_awaited_once_with(db, workline_id=7, evidence_id=9)
    reader.for_operation.assert_awaited_once_with(db, workline_id=7, operation_id=record.intent.operation_id)
    assert execution.status == status


@pytest.mark.asyncio
async def test_for_evidence_propagates_history_integrity_violation() -> None:
    """history() 的 ValueError 必须保留为 evidence 的重试/对账信号。"""
    reader = SimpleNamespace(
        operation_id_for_evidence=AsyncMock(return_value="019f3406-2200-7b03-8b01-000000000003"),
        for_operation=AsyncMock(side_effect=ValueError("drain history checkpoint Evidence missing or unpublished")),
    )
    executions = SimpleNamespace(
        for_evidence=AsyncMock(return_value=SimpleNamespace(drain_operation_id="019f3406-2200-7b03-8b01-000000000003"))
    )
    repository = DrainRepository(reader, executions=executions)

    with pytest.raises(ValueError, match="checkpoint Evidence missing or unpublished"):
        await repository.for_evidence(object(), 7, 9)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "EXCEPTION_RETIRED"])
async def test_terminal_execution_never_becomes_current(status):
    reader = SimpleNamespace(pending_for_workline=AsyncMock(return_value=None), history=AsyncMock())
    executions = SimpleNamespace(active_for_workline=AsyncMock(return_value=None))
    repository = DrainRepository(reader, executions=executions)
    assert await repository.current(object(), 7) is None
    reader.history.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_fallback_to_closed_predecessor():
    reader = SimpleNamespace(pending_for_workline=AsyncMock(return_value=None), history=AsyncMock())
    executions = SimpleNamespace(active_for_workline=AsyncMock(return_value=None))
    assert await DrainRepository(reader, executions=executions).current(object(), 7) is None
    reader.history.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_completion_requires_each_reserved_rack_departure():
    from datetime import datetime

    record = _record(
        sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)), sdk.RackFaceSequence("R2", ("270",))))
    )
    execution = SimpleNamespace(status="ACTIVE")
    executions = SimpleNamespace(
        for_evidence=AsyncMock(return_value=execution), close_in_session=AsyncMock(return_value=True)
    )
    repository = DrainRepository(SimpleNamespace(), executions=executions)
    repository.transport = AsyncMock(side_effect=[SimpleNamespace(status="ACCEPTED"), None])
    db, now = object(), datetime(2026, 10, 11)
    assert not await repository.complete_if_departed(db, record, closed_at=now)
    executions.close_in_session.assert_not_awaited()
    repository.transport.side_effect = [SimpleNamespace(status="ACCEPTED"), SimpleNamespace(status="SUCCEEDED")]
    assert await repository.complete_if_departed(db, record, closed_at=now)
    executions.close_in_session.assert_awaited_once_with(db, execution, status="COMPLETED", closed_at=now)
