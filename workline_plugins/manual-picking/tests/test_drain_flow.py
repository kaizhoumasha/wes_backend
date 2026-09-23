from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
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
    return SimpleNamespace(id=7, line_code="LINE-1", position_bindings={"OUTLET": {"location_id": "OUTLET"}})


def _flow(*, current=None, rows=(), active_task=False, completed_task=True):
    repository = SimpleNamespace(
        current=AsyncMock(return_value=current), has_completed_task=AsyncMock(return_value=completed_task)
    )
    passages = SimpleNamespace(ready_return_prefix_for_update=AsyncMock(return_value=list(rows)))
    prepare = SimpleNamespace(prepare_next_in_session=AsyncMock(return_value=SimpleNamespace(prepared=False)))
    scheduler = SimpleNamespace(create_in_session=AsyncMock())
    batch_scheduler = SimpleNamespace(create_in_session=AsyncMock())
    history = SimpleNamespace(
        has_unclosed_return=AsyncMock(return_value=False),
        latest_return=AsyncMock(return_value=None),
    )
    tasks = SimpleNamespace(has_active_for_workline=AsyncMock(return_value=active_task))
    flow = ManualPickingDrainFlow(
        repository,
        passages,
        prepare,
        scheduler,
        batch_scheduler,
        history,
        tasks=tasks,
        uuid_factory=lambda: "019f3406-2200-7b03-8b01-000000000003",
    )
    return flow, scheduler, batch_scheduler, history


@pytest.mark.asyncio
async def test_decide_freezes_only_workline_and_required_slot_count() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(bin_code="B1"), SimpleNamespace(bin_code="B2")))

    assert await flow.decide_in_session(object(), _line(), NOW) == (1, None)
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent == sdk.ReturnBufferDrainIntent(
        operation_id="019f3406-2200-7b03-8b01-000000000003",
        workline_code="LINE-1",
        required_slot_count=2,
    )


@pytest.mark.asyncio
async def test_decide_allows_ready_bins_to_drain_during_active_task_when_workstation_is_empty() -> None:
    flow, scheduler, _, _ = _flow(
        rows=(SimpleNamespace(bin_code="B1"),),
        active_task=True,
        completed_task=False,
    )

    assert await flow.decide_in_session(object(), _line(), NOW, allow_active_task=True) == (1, None)
    scheduler.create_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_decide_keeps_active_task_gate_when_workstation_has_rack() -> None:
    flow, scheduler, _, _ = _flow(rows=(SimpleNamespace(bin_code="B1"),), active_task=True)

    assert await flow.decide_in_session(object(), _line(), NOW, allow_active_task=False) == (0, None)
    scheduler.create_in_session.assert_not_awaited()


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
    flow, scheduler, _, _ = _flow(current=current, rows=(SimpleNamespace(bin_code="B1"),))

    assert await flow.decide_in_session(object(), _line(), NOW) == (0, current)
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_batch_advances_to_the_next_ordered_face() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90", "270")),))
    decision = SimpleNamespace(result=ready, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=(SimpleNamespace(bin_code="B1"),))
    history.latest_return.side_effect = [
        (sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)), NOW),
        None,
    ]

    assert await flow.active_rack_face(object(), _line(), decision, "R1") == ("R1", "270", False)


@pytest.mark.asyncio
async def test_no_batch_on_all_reserved_faces_exhausts_the_last_rack() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)), sdk.RackFaceSequence("R2", ("270",))))
    decision = SimpleNamespace(result=ready, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=(SimpleNamespace(bin_code="B1"),))
    history.latest_return.return_value = (sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)), NOW)

    assert await flow.active_rack_face(object(), _line(), decision, "R2") == ("R2", "270", True)


@pytest.mark.asyncio
async def test_return_history_before_current_drain_does_not_exhaust_reserved_face() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    current = decision(ready)
    flow, _, _, history = _flow(rows=(SimpleNamespace(bin_code="B1"),))
    history.latest_return.return_value = (
        sdk.BinReturnBatchOutcome(sdk.BinBatchNoBatch(1000)),
        NOW - timedelta(seconds=1),
    )

    assert await flow.active_rack_face(object(), _line(), current, "R1") == ("R1", "90", False)


@pytest.mark.asyncio
async def test_no_batch_is_terminal_and_never_retries_return_batch() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    current = decision(ready)
    flow, _, scheduler, history = _flow(current=current, rows=(SimpleNamespace(bin_code="B1"),))
    flow.active_rack_face = AsyncMock(return_value=("R1", "90", True))

    assert not await flow.return_in_session(object(), _line(), current, "R1", "OUTLET", NOW)
    history.latest_return.assert_not_awaited()
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclosed_return_obligation_keeps_original_identity() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    current = decision(ready)
    flow, _, scheduler, history = _flow(current=current, rows=(SimpleNamespace(bin_code="B1"),))
    history.has_unclosed_return.return_value = True
    db = object()

    assert not await flow.return_in_session(db, _line(), current, "R1", "OUTLET", NOW)
    history.has_unclosed_return.assert_awaited_once_with(
        db,
        workline_id=7,
        rack_id="R1",
        rack_face="90",
    )
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_return_batch_allows_departure_after_passage_is_closed() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    decision = SimpleNamespace(result=ready, completed_at=NOW - timedelta(seconds=1))
    flow, _, _, history = _flow(rows=())
    history.latest_return.return_value = (SimpleNamespace(result=object()), NOW)

    assert await flow.active_rack_face(object(), _line(), decision, "R1") == ("R1", "90", False)


@pytest.mark.asyncio
async def test_decide_passes_default_limit_to_passages() -> None:
    flow, _, _, _ = _flow(rows=(SimpleNamespace(bin_code="B1"),))

    await flow.decide_in_session(object(), _line(), NOW)

    assert flow._passages.ready_return_prefix_for_update.await_args.kwargs["limit"] == 4


@pytest.mark.asyncio
async def test_trigger_full_drain_asks_for_the_full_return_buffer_not_just_one_batch() -> None:
    flow, scheduler, _, _ = _flow(rows=tuple(SimpleNamespace(bin_code=f"B{i}") for i in range(10)))

    await flow.trigger_full_drain_in_session(object(), _line(), NOW)

    assert flow._passages.ready_return_prefix_for_update.await_args.kwargs["limit"] == FULL_DRAIN_LIMIT
    intent = scheduler.create_in_session.await_args.args[1]
    assert intent.required_slot_count == 10


@pytest.mark.asyncio
async def test_return_batch_uses_current_reserved_face_and_fifo_candidates() -> None:
    ready = sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))
    decision = SimpleNamespace(result=ready)
    flow, _, scheduler, _ = _flow(rows=(SimpleNamespace(bin_code="B1"),))

    assert await flow.return_in_session(object(), _line(), decision, "R1", "OUTLET", NOW)
    intent = scheduler.create_in_session.await_args.args[1]
    assert (intent.rack_id, intent.rack_face) == ("R1", "90")
    assert [candidate.bin_code for candidate in intent.return_candidates] == ["B1"]
