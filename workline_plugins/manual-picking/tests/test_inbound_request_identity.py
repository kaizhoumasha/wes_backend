"""迟到批次响应只消费原请求冻结的本地来源归属。"""

from datetime import datetime
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
import wes_plugin_sdk as sdk
from drain_execution_support import unbound_drain_executions
from manual_picking.application.batch_progress_model import ManualPickingInboundBatch
from sqlalchemy import select
from test_batch_flow import _Inbound, _Passages, _Repository, _Scheduler
from test_batch_repository import _new_sessions, _seed_current_inbound
from test_scan_flow import _setup, _wms

from src.app.execution.plugin_binding import BusinessEvidenceDisposition


def _intent():
    return sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="original-batch", task_id="PICK-001", rack_id="RACK-1", rack_face="90"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("result_name", ["INBOUND_READY", "RACK_FACE_DONE"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("manual_arrival", [None, "original", "newer"])
async def test_delayed_batch_keeps_original_member(result_name, cancelled, manual_arrival):
    intent = _intent()
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value=result_name))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    original = SimpleNamespace(
        plan_revision=5,
        source_evidence_id=11,
        rack_id="RACK-1",
        rack_face="90",
        cancelled_evidence_id=45 if cancelled else None,
    )
    newer = SimpleNamespace(
        plan_revision=13,
        source_evidence_id=12,
        rack_id="RACK-1",
        rack_face="90",
        cancelled_evidence_id=None,
    )
    flow._source_racks.list_bin_source_racks = AsyncMock(return_value=[newer, original])
    flow._batch_progress.inbound_request = AsyncMock(
        return_value=SimpleNamespace(
            operation_id=intent.operation_id,
            workline_id=7,
            picking_task_id=31,
            task_id=intent.task_id,
            rack_id=intent.rack_id,
            rack_face=intent.rack_face,
            plan_revision=5,
        )
    )
    if manual_arrival:
        fact = SimpleNamespace(
            picking_task_id=31,
            plan_revision=13 if manual_arrival == "newer" else 5,
            source_evidence_id=12 if manual_arrival == "newer" else 11,
            rack_id="RACK-1",
            rack_face="90",
            location_code="FIVE-RACK-POSITION",
            source_transport_task_id="original-rack-in",
        )
        flow._batch_progress.manual_source_arrival = AsyncMock(return_value=fact)
        flow._batch_progress.working_rack_position = AsyncMock(return_value=fact)
    evidence = _wms(30, "WMS_RESULT", intent.operation_id, {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[30] = evidence

    applied = await flow.apply_in_session(object(), 30, workline_id=7)

    if manual_arrival == "newer":
        assert applied.disposition is BusinessEvidenceDisposition.DEFERRED
        result.apply_inbound_in_session.assert_not_awaited()
    else:
        assert applied.disposition is BusinessEvidenceDisposition.APPLIED
        assert result.apply_inbound_in_session.await_args.kwargs["plan_revision"] == 5
    flow._batch_progress.inbound_request.assert_awaited_once_with(
        ANY,
        workline_id=7,
        operation_id=intent.operation_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [None, "operation_id", "workline_id", "picking_task_id", "task_id", "rack_id", "rack_face", "plan_revision"],
)
async def test_missing_or_mismatched_frozen_request_cannot_fall_back_to_current_member(changed):
    intent = _intent()
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value="INBOUND_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    values = {
        "operation_id": intent.operation_id,
        "workline_id": 7,
        "picking_task_id": 31,
        "task_id": intent.task_id,
        "rack_id": intent.rack_id,
        "rack_face": intent.rack_face,
        "plan_revision": 1,
    }
    if changed:
        values[changed] = 999 if isinstance(values[changed], int) else "other"
    flow._batch_progress.inbound_request = AsyncMock(return_value=SimpleNamespace(**values) if changed else None)
    evidence = _wms(30, "WMS_RESULT", intent.operation_id, {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[30] = evidence

    assert (
        await flow.apply_in_session(object(), 30, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    result.apply_inbound_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_freezes_local_member_before_scheduling_without_wire_revision():
    events = []
    repo = _Repository()
    repo.record_allocation = AsyncMock(side_effect=lambda *a, **kw: events.append(("freeze", kw)))
    scheduler = _Scheduler()
    scheduler.create_in_session = AsyncMock(side_effect=lambda *a, **kw: events.append(("schedule", kw)))
    flow = import_module("manual_picking.application.batch_flow").ManualPickingBatchFlow(
        repo,
        _Passages(()),
        scheduler,
        _Inbound(),
        uuid_factory=lambda: "original-batch",
        drain_executions=unbound_drain_executions(),
    )
    assert await flow.advance_in_session(
        object(),
        workline_id=7,
        workline_code="LINE-1",
        picking_task_id=31,
        task_id="PICK-001",
        plan_revision=5,
        source_evidence_id=11,
        rack_id="RACK-1",
        rack_face="90",
        return_location="RETURN",
        inlet_location="INLET",
        now=datetime(2026, 10, 28),
    )
    assert [event[0] for event in events] == ["freeze", "schedule"]
    assert events[0][1] == {
        "workline_id": 7,
        "picking_task_id": 31,
        "plan_revision": 5,
        "intent": _intent(),
        "evidence_id": None,
    }
    assert not hasattr(events[0][1]["intent"], "plan_revision")


@pytest.mark.asyncio
async def test_pending_request_persists_and_attaches_only_original_response():
    engine, sessions = await _new_sessions()
    repo = import_module("manual_picking.application.batch_repository").BatchRepository()
    intent = _intent()
    try:
        async with sessions.begin() as db:
            await repo.record_allocation(
                db, workline_id=7, picking_task_id=31, plan_revision=5, intent=intent, evidence_id=None
            )
        async with sessions.begin() as db:
            frozen = await repo.inbound_request(db, workline_id=7, operation_id=intent.operation_id)
            assert (frozen.plan_revision, frozen.response_evidence_id) == (5, None)
            assert await repo.inbound_request(db, workline_id=8, operation_id=intent.operation_id) is None
            assert (
                await repo.inbound_progress(db, 7, intent.task_id, 5, intent.rack_id, intent.rack_face, "INLET") is None
            )
            with pytest.raises(ValueError, match="identity changed"):
                await repo.record_allocation(
                    db, workline_id=7, picking_task_id=31, plan_revision=13, intent=intent, evidence_id=None
                )
            await _seed_current_inbound(db, intent, sdk.BinInboundBatchRackFaceDone(), plan_revision=5)
        async with sessions.begin() as db:
            frozen = await repo.inbound_request(db, workline_id=7, operation_id=intent.operation_id)
            assert (frozen.plan_revision, frozen.response_evidence_id) == (5, 31)
            assert len((await db.scalars(select(ManualPickingInboundBatch))).all()) == 1
    finally:
        await engine.dispose()
