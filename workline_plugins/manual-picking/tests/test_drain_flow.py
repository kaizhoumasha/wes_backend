"""排空只在下一任务 claim 失败后创建，可靠链不可被后来任务越过。"""

from datetime import datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk

NOW = datetime(2026, 9, 15, 12)
OP = "019f0000-0000-7000-8000-000000000001"
NEXT = "019f0000-0000-7000-8000-000000000002"


def setup_flow():
    module = import_module("manual_picking.application.drain_flow")
    repository = SimpleNamespace(current=AsyncMock(return_value=None), has_completed_task=AsyncMock(return_value=True))
    passages = SimpleNamespace(ready_return_prefix_for_update=AsyncMock(return_value=(SimpleNamespace(bin_code="B1"),)))
    tasks = SimpleNamespace(has_active_for_workline=AsyncMock(return_value=False))
    prepare = SimpleNamespace(prepare_next_in_session=AsyncMock(return_value=SimpleNamespace(prepared=False)))
    scheduler = SimpleNamespace(create_in_session=AsyncMock())
    batch_scheduler = SimpleNamespace(create_in_session=AsyncMock())
    history = SimpleNamespace(latest_return=AsyncMock(return_value=None))
    flow = module.ManualPickingDrainFlow(
        repository, passages, prepare, scheduler, batch_scheduler, history, tasks=tasks, uuid_factory=lambda: NEXT
    )
    line = SimpleNamespace(
        id=7, line_code="LINE-1", plugin_key="manual-picking", position_bindings={"OUTLET": {"location_id": "OUTLET"}}
    )
    return flow, repository, passages, tasks, prepare, scheduler, batch_scheduler, history, line


def decision(result=None, *, status="COMPLETED", published=True):
    intent = sdk.wms_operations.workline_return_buffer_drain_rack_decide(
        operation_id=OP,
        workline_code="LINE-1",
        plugin_key="manual-picking",
        drain_reason="PICKING_TASK_COMPLETED",
        return_candidates=(sdk.BinReturnCandidate(1, "B1", "OUTLET"),),
    )
    return SimpleNamespace(
        intent=intent,
        result=result,
        evidence_id=51 if published else None,
        status=status,
        completed_at=NOW,
        workline_id=7,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active,claimed", [(True, False), (False, True)])
async def test_next_task_wins_before_first_drain(active, claimed):
    flow, _repo, _passages, tasks, prepare, scheduler, *_rest, line = setup_flow()
    tasks.has_active_for_workline.return_value = active
    prepare.prepare_next_in_session.return_value.prepared = claimed
    db = object()
    count, ready = await flow.decide_in_session(db, line, NOW)
    assert count == int(claimed)
    assert ready is None
    scheduler.create_in_session.assert_not_awaited()
    if claimed:
        prepare.prepare_next_in_session.assert_awaited_once_with(db, line, now=NOW)
    else:
        prepare.prepare_next_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_next_and_later_scan4_creates_one_workline_drain():
    flow, repo, passages, _tasks, _prepare, scheduler, *_rest, line = setup_flow()
    passages.ready_return_prefix_for_update.return_value = ()
    assert await flow.decide_in_session(object(), line, NOW) == (0, None)
    passages.ready_return_prefix_for_update.return_value = (SimpleNamespace(bin_code="B1"),)
    assert await flow.decide_in_session(object(), line, NOW) == (1, None)
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent.operation_id == NEXT and intent.previous_operation_id is None
    assert intent.return_candidates == (sdk.BinReturnCandidate(1, "B1", "OUTLET"),)
    assert scheduler.create_in_session.await_args.kwargs["workline_id"] == 7
    repo.current.return_value = decision(status="PENDING", published=False)
    assert await flow.decide_in_session(object(), line, NOW) == (0, None)
    assert scheduler.create_in_session.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "DISPATCHING", "RECONCILING", "WAIT", "READY"])
async def test_existing_drain_cannot_be_superseded_by_later_task(state):
    flow, repo, _passages, tasks, prepare, scheduler, *_rest, line = setup_flow()
    result = (
        sdk.ReturnBufferDrainWait(1000)
        if state == "WAIT"
        else (sdk.ReturnBufferDrainReady("DR1", "90") if state == "READY" else None)
    )
    row = decision(result, status="COMPLETED" if result else state, published=bool(result))
    repo.current.return_value = row
    tasks.has_active_for_workline.return_value = True
    count, ready = await flow.decide_in_session(object(), line, NOW)
    assert count == 0
    assert ready is (row if state == "READY" else None)
    prepare.prepare_next_in_session.assert_not_awaited()
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_due_freezes_new_prefix_and_direct_predecessor():
    flow, repo, passages, _tasks, prepare, scheduler, *_rest, line = setup_flow()
    repo.current.return_value = decision(sdk.ReturnBufferDrainWait(1000))
    assert await flow.decide_in_session(object(), line, NOW + timedelta(milliseconds=999)) == (0, None)
    passages.ready_return_prefix_for_update.return_value = (
        SimpleNamespace(bin_code="B2"),
        SimpleNamespace(bin_code="B3"),
    )
    assert await flow.decide_in_session(object(), line, NOW + timedelta(seconds=1)) == (1, None)
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent.operation_id == NEXT and intent.previous_operation_id == OP
    assert [x.bin_code for x in intent.return_candidates] == ["B2", "B3"]
    assert scheduler.create_in_session.await_args.kwargs["created_at"] == NOW + timedelta(seconds=1)
    prepare.prepare_next_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_drain_return_uses_current_face_fifo_and_respects_no_batch_due():
    flow, _repo, _passages, _tasks, _prepare, _scheduler, batch_scheduler, history, line = setup_flow()
    ready = decision(sdk.ReturnBufferDrainReady("DR1", "90"))
    assert await flow.return_in_session(object(), line, ready, "OUTLET", NOW)
    intent = batch_scheduler.create_in_session.await_args.args[1]
    assert intent.rack_id == "DR1" and intent.rack_face == "90"
    assert intent.return_candidates == (sdk.BinReturnCandidate(1, "B1", "OUTLET"),)
    history.latest_return.return_value = (SimpleNamespace(result=sdk.BinBatchNoBatch(1000)), NOW)
    assert not await flow.return_in_session(object(), line, ready, "OUTLET", NOW)
    assert await flow.return_in_session(object(), line, ready, "OUTLET", NOW + timedelta(seconds=1))
    assert batch_scheduler.create_in_session.await_count == 2


@pytest.mark.asyncio
async def test_drain_driver_window_arrival_fifo_and_empty_line_departure():
    from test_source_progression import setup_driver

    driver, line, _task, _positions, _plans, _flow, creator, *_ = setup_driver()
    row = decision(sdk.ReturnBufferDrainReady("R1", "90"))
    row.workline_id = 7
    repo = SimpleNamespace(
        transport=AsyncMock(return_value=None),
        arrival_matches=AsyncMock(return_value=True),
        has_unclosed_rack_action=AsyncMock(return_value=False),
    )
    drain = SimpleNamespace(
        repository=repo,
        decide_in_session=AsyncMock(return_value=(0, row)),
        return_in_session=AsyncMock(return_value=False),
    )
    driver._drain = drain
    driver._passages.unfinished_return_prefix_for_update = AsyncMock(return_value=())
    creator.create = AsyncMock()
    assert await driver.advance_completed_in_session(object(), line) == 0  # window full
    driver._rack_cycles.occupied_source_rack_ids.return_value = set()
    assert await driver.advance_completed_in_session(object(), line) == 1
    created = creator.create.await_args.kwargs
    assert created["step"] == "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN"
    assert created["correlation_id"] == f"drain:{OP}" and created["source_evidence_id"] == 51
    assert (
        created["intent"].target_face == "90" and created["intent"].rcs_template_id == sdk.TransportRcsTemplateId.CTU01
    )
    ingress = SimpleNamespace(status="PENDING", transport_task_id="arrival-1")

    async def lookup(_db, _decision, step):
        return ingress if step.endswith("_IN") else None

    repo.transport.side_effect = lookup
    assert await driver.advance_completed_in_session(object(), line) == 0
    assert creator.create.await_count == 1
    ingress.status = "SUCCEEDED"
    repo.arrival_matches.return_value = False
    assert await driver.advance_completed_in_session(object(), line) == 0
    repo.arrival_matches.return_value = True
    drain.return_in_session.return_value = True
    assert await driver.advance_completed_in_session(object(), line) == 1
    assert not creator.depart
    drain.return_in_session.return_value = False
    driver._passages.has_bin_before_return_buffer.return_value = True
    assert await driver.advance_completed_in_session(object(), line) == 0
    driver._passages.has_bin_before_return_buffer.return_value = False
    driver._passages.unfinished_return_prefix_for_update.return_value = (SimpleNamespace(return_state="REQUESTED"),)
    assert await driver.advance_completed_in_session(object(), line) == 0
    driver._passages.unfinished_return_prefix_for_update.return_value = ()
    assert await driver.advance_completed_in_session(object(), line) == 1
    assert creator.depart[0]["step"] == "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"
    assert creator.depart[0]["correlation_id"] == f"drain:{OP}"
    assert creator.depart[0]["rack_id"] == "R1" and creator.depart[0]["source_evidence_id"] == 51
    assert not creator.rotate
    repo.transport.side_effect = None
    repo.transport.return_value = SimpleNamespace(status="PENDING")  # CTU03 binding already exists
    assert await driver.advance_completed_in_session(object(), line) == 0
    assert len(creator.depart) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["batch", "rack"])
async def test_drain_does_not_bypass_unclosed_obligations(gate):
    from test_source_progression import setup_driver

    driver, line, _task, _positions, _plans, flow, creator, *_ = setup_driver()
    row = decision(sdk.ReturnBufferDrainReady("R1", "90"))
    repo = SimpleNamespace(
        transport=AsyncMock(return_value=None), has_unclosed_rack_action=AsyncMock(return_value=gate == "rack")
    )
    driver._drain = SimpleNamespace(repository=repo, decide_in_session=AsyncMock(return_value=(0, row)))
    driver._rack_cycles.occupied_source_rack_ids.return_value = set()
    flow.busy = gate == "batch"
    creator.create = AsyncMock()
    assert await driver.advance_completed_in_session(object(), line) == 0
    creator.create.assert_not_awaited()
    if gate == "batch":
        driver._drain.decide_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_composition_reserves_prepare_but_keeps_drain_activation_and_fact_wakes(monkeypatch):
    from deployment import plugin_composition

    monkeypatch.setattr(plugin_composition, "build_execution_runtime", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        plugin_composition,
        "IntegrationRunWorkLineOwner",
        lambda: SimpleNamespace(
            is_reserved=AsyncMock(return_value=False), owns_operation=AsyncMock(return_value=False)
        ),
    )
    transport = SimpleNamespace(service=object(), client=AsyncMock(), position_projection_service=object())
    runtime = plugin_composition.build_deployment_runtime(
        session_factory=object(),
        transport_runtime=transport,
        device_command_service=object(),
        enabled_plugin_keys=("manual-picking",),
    )
    driver = runtime.plugins[0].picking_task_batch_driver
    driver._drain.repository.current = AsyncMock(return_value=decision(status="PENDING", published=False))
    assert await runtime.picking_task_prepare_service._workline_reserved(object(), 7)
    assert not await runtime.picking_task_plan_activation_service._workline_reserved(object(), 7)
    assert not await runtime.execution.workline_reserved(object(), 7)
    assert "workline.return_buffer.drain_rack_decide@v1" in runtime.plugins[0].runtime_binding.business_wms_operations
    driver._drain.repository.current.return_value = None
    assert not await runtime.picking_task_prepare_service._workline_reserved(object(), 7)


@pytest.mark.asyncio
async def test_no_completion_never_creates_drain_and_frozen_location_cannot_drift():
    flow, repo, _passages, _tasks, _prepare, scheduler, *_rest, line = setup_flow()
    repo.has_completed_task.return_value = False
    assert await flow.decide_in_session(object(), line, NOW) == (0, None)
    scheduler.create_in_session.assert_not_awaited()
    repo.current.return_value = decision(sdk.ReturnBufferDrainReady("R1", "90"))
    line.position_bindings["OUTLET"]["location_id"] = "CHANGED"
    with pytest.raises(ValueError, match="binding"):
        await flow.decide_in_session(object(), line, NOW)
