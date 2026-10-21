from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from drain_execution_support import unbound_drain_executions
from manual_picking.application.drain_flow import FULL_DRAIN_LIMIT, ManualPickingDrainFlow

NOW = datetime(2026, 9, 17)


def decision(result):
    return SimpleNamespace(
        workline_id=7,
        status="COMPLETED",
        created_at=NOW,
        completed_at=NOW,
        intent=sdk.ReturnBufferDrainIntent(
            operation_id="019f3406-2200-7b03-8b01-000000000003",
            workline_code="LINE-1",
            required_slot_count=2,
        ),
        result=result,
        evidence_id=9,
    )


def _line():
    return SimpleNamespace(
        id=7,
        line_code="LINE-1",
        position_bindings={"OUTLET": {"location_id": "OUTLET"}, "FIVE_RACK": {"location_id": "FIVE-POS"}},
    )


def _flow(*, current=None, rows=(), active_task=False, five_rack_window_active=True):
    repository = SimpleNamespace(current=AsyncMock(return_value=current))
    passages = SimpleNamespace(ready_prefix_for_update=AsyncMock(return_value=list(rows)))
    prepare = SimpleNamespace(prepare_next_in_session=AsyncMock(return_value=SimpleNamespace(prepared=False)))
    scheduler = SimpleNamespace(create_in_session=AsyncMock())
    batch_scheduler = SimpleNamespace(create_in_session=AsyncMock())
    history = SimpleNamespace(
        latest_return=AsyncMock(return_value=None),
    )
    executions = unbound_drain_executions()

    async def original_return(_db, *, execution_id, workline_id, rack_id, rack_face):
        return await history.latest_return(_db, workline_id=workline_id, rack_id=rack_id, rack_face=rack_face)

    executions.latest_return_evidence_for_execution = AsyncMock(side_effect=original_return)
    history.read_return = AsyncMock(side_effect=lambda _db, outcome, *, workline_id: (None, outcome))
    tasks = SimpleNamespace(
        has_active_for_workline=AsyncMock(return_value=active_task),
        get_active_for_workline=AsyncMock(return_value=SimpleNamespace(id=31) if active_task else None),
    )
    bindings = SimpleNamespace(
        list_active_window_for_target=AsyncMock(
            return_value=[SimpleNamespace(step="PICKING_TASK_BIN_SOURCE_RACK_IN", picking_task_id=31)]
            if five_rack_window_active
            else []
        )
    )
    flow = ManualPickingDrainFlow(
        repository,
        passages,
        prepare,
        scheduler,
        batch_scheduler,
        history,
        tasks=tasks,
        bindings=bindings,
        uuid_factory=lambda: "019f3406-2200-7b03-8b01-000000000003",
        drain_executions=executions,
    )
    return flow, scheduler, batch_scheduler, history


@pytest.mark.asyncio
async def test_decide_freezes_only_workline_and_required_slot_count() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"), SimpleNamespace(bin_code="B2")))

    assert await flow.decide_in_session(object(), _line(), NOW) == (1, None)
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent == sdk.ReturnBufferDrainIntent(
        operation_id="019f3406-2200-7b03-8b01-000000000003",
        workline_code="LINE-1",
        required_slot_count=2,
    )


@pytest.mark.asyncio
async def test_decide_keeps_active_task_gate_when_five_rack_window_is_occupied() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),), active_task=True)

    assert await flow.decide_in_session(object(), _line(), NOW) == (0, None)
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_current_drain_ignores_residual_drain_window() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),), active_task=True)
    flow._bindings.list_active_window_for_target.return_value = [
        SimpleNamespace(step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN")
    ]

    db = SimpleNamespace(flush=AsyncMock())
    assert await flow.decide_in_session(db, _line(), NOW) == (1, None)
    assert flow._bindings.list_active_window_for_target.return_value[0].window_released_at is not None
    db.flush.assert_awaited_once()
    scheduler.create_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_residual_drain_window_does_not_hide_current_source_window() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),), active_task=True)
    flow._bindings.list_active_window_for_target.return_value.append(
        SimpleNamespace(step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN")
    )

    assert await flow.decide_in_session(SimpleNamespace(flush=AsyncMock()), _line(), NOW) == (0, None)
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_fails_closed_when_active_task_has_no_five_rack_target() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),), active_task=True)
    line = _line()
    line.position_bindings.pop("FIVE_RACK")

    assert await flow.decide_in_session(object(), line, NOW) == (0, None)
    flow._bindings.list_active_window_for_target.assert_not_awaited()
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_allows_ready_bins_to_drain_during_active_task_when_five_rack_window_is_empty() -> None:
    """R6：插缝按 FIVE_RACK 容量窗口是否被占用判断，不用全局位置投影数量（红队复核）。"""
    flow, scheduler, _, _ = _flow(
        rows=(SimpleNamespace(id=41, bin_code="B1"),),
        active_task=True,
        five_rack_window_active=False,
    )

    assert await flow.decide_in_session(object(), _line(), NOW) == (1, None)
    scheduler.create_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_fifo_without_active_task_does_not_depend_on_historical_completed_task() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))

    assert await flow.decide_in_session(object(), _line(), NOW) == (1, None)
    scheduler.create_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_reservation_is_reused_without_creating_a_new_decision() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),))
    current = SimpleNamespace(
        intent=sdk.ReturnBufferDrainIntent(
            operation_id="019f3406-2200-7b03-8b01-000000000003",
            workline_code="LINE-1",
            required_slot_count=2,
        ),
        result=ready,
        completed_at=NOW,
    )
    flow, scheduler, _, _ = _flow(current=current, rows=(SimpleNamespace(id=41, bin_code="B1"),))

    assert await flow.decide_in_session(object(), _line(), NOW) == (0, current)
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_batch_advances_to_the_next_ordered_face() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),))
    decision = SimpleNamespace(result=ready, evidence_id=9, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))
    history.latest_return.side_effect = [
        (sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)), NOW),
        None,
    ]

    assert await flow.active_rack_face(object(), _line(), decision, "R1") == ("R1", "270", False)


@pytest.mark.asyncio
async def test_no_batch_on_all_reserved_faces_exhausts_the_last_rack() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)), sdk.RackFaceSequence("R2", ("270",))))
    decision = SimpleNamespace(result=ready, evidence_id=9, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))
    history.latest_return.return_value = (sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)), NOW)

    assert await flow.active_rack_face(object(), _line(), decision, "R2") == ("R2", "270", True)


@pytest.mark.asyncio
async def test_reserved_rack_waits_when_another_rack_empties_return_buffer() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)), sdk.RackFaceSequence("R2", ("270",))))
    decision = SimpleNamespace(result=ready, evidence_id=9, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=())
    history.latest_return.return_value = None

    assert await flow.active_rack_face(object(), _line(), decision, "R2") == ("R2", "270", False)


@pytest.mark.asyncio
async def test_untried_face_waits_after_prior_no_batch_when_return_buffer_has_no_ready_bins() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),))
    decision = SimpleNamespace(result=ready, evidence_id=9, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=())
    history.latest_return.side_effect = [(sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)), NOW), None]

    assert await flow.active_rack_face(object(), _line(), decision, "R1") == ("R1", "90", False)


@pytest.mark.asyncio
async def test_unpublished_current_face_request_cannot_fall_back_to_exhausted_face():
    current = decision(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),)))
    flow, _, _, history = _flow(rows=())
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="pending-270",
        workline_code="LINE-1",
        rack_id="R1",
        rack_face="270",
        return_candidates=(sdk.BinReturnCandidate(1, "B1", "OUTLET"),),
    )
    flow._drain_executions.pending_return_for_rack.return_value = (intent, None, None)
    assert await flow.active_rack_face(object(), _line(), current, "R1") == ("R1", "270", False)
    history.latest_return.assert_not_awaited()


@pytest.mark.asyncio
async def test_return_history_before_current_drain_does_not_exhaust_reserved_face() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    current = decision(ready)
    flow, _, _, history = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))
    history.latest_return.return_value = (
        sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)),
        NOW - timedelta(seconds=1),
    )

    flow._drain_executions.latest_return_evidence_for_execution = AsyncMock(return_value=None)

    assert await flow.active_rack_face(object(), _line(), current, "R1") == ("R1", "90", False)


@pytest.mark.asyncio
async def test_no_batch_is_terminal_and_never_retries_return_batch() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    current = decision(ready)
    flow, _, scheduler, history = _flow(current=current, rows=(SimpleNamespace(id=41, bin_code="B1"),))
    flow.active_rack_face = AsyncMock(return_value=("R1", "90", True))

    assert not await flow.return_in_session(object(), _line(), current, "R1", "OUTLET", NOW)
    history.latest_return.assert_not_awaited()
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_return_batch_allows_departure_after_passage_is_closed() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    decision = SimpleNamespace(result=ready, evidence_id=9, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=())
    history.latest_return.return_value = (SimpleNamespace(result=object()), NOW)

    assert await flow.active_rack_face(object(), _line(), decision, "R1") == ("R1", "90", True)


@pytest.mark.asyncio
async def test_decide_passes_default_limit_to_passages() -> None:
    flow, _, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))

    await flow.decide_in_session(object(), _line(), NOW)

    assert flow._passages.ready_prefix_for_update.await_args.kwargs["limit"] == 4


@pytest.mark.asyncio
async def test_trigger_full_drain_asks_for_the_full_return_buffer_not_just_one_batch() -> None:
    flow, scheduler, _, _ = _flow(rows=tuple(SimpleNamespace(bin_code=f"B{i}") for i in range(10)))

    await flow.trigger_full_drain_in_session(object(), _line(), NOW)

    assert flow._passages.ready_prefix_for_update.await_args.kwargs["limit"] == FULL_DRAIN_LIMIT
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent.required_slot_count == 10


@pytest.mark.asyncio
async def test_return_batch_uses_current_reserved_face_and_fifo_candidates() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    decision = SimpleNamespace(result=ready, evidence_id=9)
    flow, _, scheduler, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))

    assert await flow.return_in_session(object(), _line(), decision, "R1", "OUTLET", NOW)
    intent = scheduler.create_in_session.await_args.args[1]
    assert (intent.rack_id, intent.rack_face) == ("R1", "90")
    assert [candidate.bin_code for candidate in intent.return_candidates] == ["B1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unapplied", [False, True])
async def test_pending_ready_reader_current_decide_keeps_one_operation_then_one_active_execution(unapplied):
    from unittest.mock import Mock

    from manual_picking.application.drain_execution_repository import DrainExecutionRepository
    from manual_picking.application.drain_repository import DrainRepository

    from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
    from src.app.wms_adapter.return_buffer_drain.typed import encode_request
    from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord, ReturnBufferDrainResultReader
    from src.utils.canonical_json import canonical_json_digest

    original = decision(None)
    request = encode_request(original.intent, timestamp=1)
    answer = {
        "operation_id": original.intent.operation_id,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "READY", "racks": [{"rack_id": "R1", "rack_face": ["90"]}]},
    }
    confirmation = SimpleNamespace(
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=original.intent.operation_id,
        workline_id=7,
        status="COMPLETED",
        response_evidence_id=9,
        response_result="READY",
        created_at=NOW,
        completed_at=NOW,
        request_payload=request,
        request_digest=canonical_json_digest(request),
    )
    evidence = SimpleNamespace(
        id=9,
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=original.intent.operation_id,
        kind=InboundEvidenceKind.WMS_RESULT,
        workline_id=7,
        normalized_payload=answer,
        payload_digest=canonical_json_digest(answer),
        apply_status=InboundEvidenceApplyStatus.RECONCILING if unapplied else InboundEvidenceApplyStatus.APPLIED,
        published_at=NOW if unapplied else None,
    )

    class Db:
        def __init__(self):
            self.executions = []
            self.add = Mock()
            self.flush = AsyncMock()

        async def scalar(self, statement):
            return self.executions[0] if self.executions else None

        async def scalars(self, statement):
            return SimpleNamespace(all=lambda: [row for row in self.executions if row.status == "ACTIVE"])

        async def execute(self, statement):
            names = [column["name"] for column in statement.column_descriptions]
            row = confirmation if names == ["operation_id", "status", "response_result"] else (confirmation, evidence)
            return SimpleNamespace(one_or_none=lambda: row)

    db = Db()
    reader = ReturnBufferDrainResultReader()
    executions = DrainExecutionRepository()
    repository = DrainRepository(reader, executions=executions)
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))
    flow.repository = repository
    for _ in range(2):
        pending = await repository.current(db, 7)
        assert pending.intent.operation_id == original.intent.operation_id
        assert pending.result is None
        assert await flow.decide_in_session(db, _line(), NOW) == (0, None)
    scheduler.create_in_session.assert_not_awaited()

    evidence.apply_status = InboundEvidenceApplyStatus.APPLIED
    applied = ReturnBufferDrainRecord(
        7, "COMPLETED", NOW, NOW, original.intent, sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),)), 9
    )
    execution = await executions.ensure_ready_in_session(db, decision=applied)
    db.executions.append(execution)
    assert await executions.ensure_ready_in_session(db, decision=applied) is execution
    evidence.published_at = NOW
    count, current = await flow.decide_in_session(db, _line(), NOW)
    assert count == 0 and current.intent.operation_id == original.intent.operation_id
    assert current.evidence_id == 9
    assert len(db.executions) == 1
    assert db.executions[0].status == "ACTIVE"
    db.add.assert_called_once_with(execution)
    scheduler.create_in_session.assert_not_awaited()
    await executions.close_in_session(db, execution, status="COMPLETED", closed_at=NOW)
    assert await repository.current(db, 7) is None


@pytest.mark.asyncio
async def test_wait_identity_is_preserved_until_retry_is_due():
    from manual_picking.application.drain_repository import DrainRepository

    wait = decision(sdk.ReturnBufferDrainWait(1000))
    reader = SimpleNamespace(pending_for_workline=AsyncMock(return_value=wait))
    executions = SimpleNamespace(active_for_workline=AsyncMock(return_value=None))
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))
    flow.repository = DrainRepository(reader, executions=executions)
    assert await flow.decide_in_session(object(), _line(), NOW) == (0, None)
    scheduler.create_in_session.assert_not_awaited()
    assert await flow.decide_in_session(object(), _line(), NOW + timedelta(seconds=1)) == (1, None)
    scheduler.create_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_request_freezes_return_execution_ids():
    current = decision(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),)))
    rows = (SimpleNamespace(id=41, bin_code="B1"), SimpleNamespace(id=42, bin_code="B2"))
    flow, _, scheduler, _ = _flow(current=current, rows=rows)
    execution = SimpleNamespace(id=81, status="ACTIVE")
    flow._drain_executions = SimpleNamespace(
        for_evidence=AsyncMock(return_value=execution),
        eligible_return_prefix=AsyncMock(return_value=rows),
        freeze_return_members_in_session=AsyncMock(),
        latest_return_evidence_for_execution=AsyncMock(return_value=None),
        pending_return_for_rack=AsyncMock(return_value=None),
    )
    db = object()
    assert await flow.return_in_session(db, _line(), current, "R1", "OUTLET", NOW)
    flow._drain_executions.freeze_return_members_in_session.assert_awaited_once_with(
        db,
        execution_id=81,
        return_batch_operation_id=scheduler.create_in_session.await_args.args[1].operation_id,
        return_ids=(41, 42),
    )


@pytest.mark.asyncio
async def test_active_pending_drain_member_is_not_requested_on_other_face():
    current = decision(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),)))
    flow, _, scheduler, _ = _flow(current=current, rows=(SimpleNamespace(id=41, bin_code="B1"),))
    flow._drain_executions = SimpleNamespace(
        for_evidence=AsyncMock(return_value=SimpleNamespace(id=81, status="ACTIVE")),
        eligible_return_prefix=AsyncMock(return_value=()),
        freeze_return_members_in_session=AsyncMock(),
        latest_return_evidence_for_execution=AsyncMock(return_value=None),
        pending_return_for_rack=AsyncMock(return_value=None),
    )
    assert not await flow.return_in_session(object(), _line(), current, "R1", "OUTLET", NOW)
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_task_no_batch_on_reused_face_does_not_change_original_drain():
    current = decision(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),)))
    flow, _, _, history = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),))
    history.latest_return.return_value = (
        sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)),
        NOW + timedelta(seconds=1),
    )
    flow._drain_executions.latest_return_evidence_for_execution = AsyncMock(return_value=None)
    assert await flow.active_rack_face(object(), _line(), current, "R1") == ("R1", "90", False)
    history.latest_return.assert_not_awaited()


def _retirement_flow(*, status="QUEUED", action_status="ACCEPTED", step="MANUAL_PICKING_RETURN_BATCH", deadline=None):
    current = decision(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),)))
    flow, _, _, _ = _flow(current=current)
    execution = SimpleNamespace(id=81, status="ACTIVE")
    task = SimpleNamespace(id=101, workline_id=7, status=status)
    action = SimpleNamespace(
        client_request_id="transport-original",
        status=action_status,
        result_deadline_at=deadline,
        reason_code=None,
        request_json={"rack_id": "R1"},
    )
    binding = SimpleNamespace(step=step, client_request_id=action.client_request_id)
    flow._drain_executions = SimpleNamespace(
        for_evidence=AsyncMock(return_value=execution),
        retirement_tasks=AsyncMock(return_value=(task,)),
        retirement_actions=AsyncMock(return_value=((binding, action, False),)),
        close_in_session=AsyncMock(return_value=True),
        release_windows_in_session=AsyncMock(),
    )
    return flow, execution, task, binding, action


@pytest.mark.asyncio
@pytest.mark.parametrize("task_status", ["QUEUED", "PREPARING", "EXECUTING"])
@pytest.mark.parametrize(
    ("action_status", "reason"),
    [
        ("FAILED", "DRAIN_ACTION_TERMINAL_EXCEPTION"),
        ("ACCEPTED", "DRAIN_RESULT_OVERDUE"),
        ("RECONCILING", "DRAIN_RESULT_UNKNOWN"),
    ],
)
async def test_exception_retirement_requires_ready_independent_task_and_blocker(task_status, action_status, reason):
    flow, execution, task, _, action = _retirement_flow(
        status=task_status, action_status=action_status, deadline=NOW - timedelta(microseconds=1)
    )
    db = object()
    assert await flow.retire_exception_in_session(db, _line(), NOW)
    flow._drain_executions.close_in_session.assert_awaited_once_with(
        db,
        execution,
        status="EXCEPTION_RETIRED",
        closed_at=NOW,
        reason_code=reason,
        blocker_client_request_id=action.client_request_id,
        trigger_picking_task_id=task.id,
    )
    flow._drain_executions.release_windows_in_session.assert_awaited_once_with(
        db,
        execution=execution,
        closed_at=NOW,
    )
    assert action.status == action_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    [
        "no_task",
        "cancelled",
        "not_ready",
        "retired",
        "before_deadline",
        "at_deadline",
        "progress",
        "send_unknown",
        "pending_final",
        "retryable_in",
        "retryable_cancelled",
        "retryable_departure_rejected",
    ],
)
async def test_drain_does_not_retire_without_current_basis(missing):
    flow, execution, task, binding, action = _retirement_flow(deadline=NOW - timedelta(seconds=1))
    if missing == "no_task":
        flow._drain_executions.retirement_tasks.return_value = ()
    elif missing == "cancelled":
        task.status = "CANCELLED"
    elif missing == "not_ready":
        flow.repository.current.return_value = decision(sdk.ReturnBufferDrainWait(1000))
    elif missing == "retired":
        execution.status = "EXCEPTION_RETIRED"
    elif missing in {"before_deadline", "at_deadline"}:
        action.result_deadline_at = NOW + (timedelta(seconds=1) if missing == "before_deadline" else timedelta())
    elif missing == "progress":
        action.status = "SUCCEEDED"
    elif missing == "send_unknown":
        action.status, action.reason_code = "PENDING", "SUBMIT_DELIVERY_UNKNOWN"
    elif missing == "pending_final":
        flow._drain_executions.retirement_actions.return_value = ((binding, action, True),)
    elif missing == "retryable_in":
        action.status, binding.step = "FAILED", "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN"
    elif missing == "retryable_departure_rejected":
        action.status, binding.step = "REJECTED", "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"
    elif missing == "retryable_cancelled":
        action.status, action.reason_code = "FAILED", "RCS_TASK_CANCELLED"
        binding.step = "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_ROTATE"
    assert not await flow.retire_exception_in_session(object(), _line(), NOW)
    flow._drain_executions.close_in_session.assert_not_awaited()
    flow._drain_executions.release_windows_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_retirement_reason_precedence_and_frozen_client_identity_are_stable():
    flow, _, _, binding, action = _retirement_flow(action_status="RECONCILING")
    terminal = SimpleNamespace(**vars(action))
    terminal.status, terminal.client_request_id = "FAILED", "transport-b"
    earlier = SimpleNamespace(**vars(terminal))
    earlier.client_request_id = "transport-a"
    overdue = SimpleNamespace(**vars(action))
    overdue.status, overdue.result_deadline_at = "ACCEPTED", NOW - timedelta(seconds=1)
    flow._drain_executions.retirement_actions.return_value = (
        (binding, action, False),
        (binding, terminal, False),
        (binding, overdue, False),
        (binding, earlier, False),
    )
    assert await flow.retire_exception_in_session(object(), _line(), NOW)
    refs = flow._drain_executions.close_in_session.await_args.kwargs
    assert refs["reason_code"] == "DRAIN_ACTION_TERMINAL_EXCEPTION"
    assert refs["blocker_client_request_id"] == "transport-a"


@pytest.mark.asyncio
async def test_matching_final_evidence_is_replayed_before_any_retirement_reason():
    flow, _, _, binding, action = _retirement_flow(deadline=NOW - timedelta(seconds=1))
    terminal = SimpleNamespace(**vars(action))
    terminal.status, terminal.client_request_id = "FAILED", "transport-b"
    flow._drain_executions.retirement_actions.return_value = ((binding, action, True), (binding, terminal, False))
    assert not await flow.retire_exception_in_session(object(), _line(), NOW)
    flow._drain_executions.close_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_task_window_does_not_block_independent_drain():
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(id=41, bin_code="B1"),), active_task=True)
    old = SimpleNamespace(step="PICKING_TASK_BIN_SOURCE_RACK_IN", picking_task_id=12, window_released_at=None)
    flow._bindings.list_active_window_for_target.return_value = [old]
    assert await flow.decide_in_session(object(), _line(), NOW) == (1, None)
    scheduler.create_in_session.assert_awaited_once()
    assert old.window_released_at is None
