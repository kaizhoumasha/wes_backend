"""连续 READY 的离线读取、供架与原子接续；不进行真实设备派发。"""

import asyncio

import pytest
from automatic_picking.application.drain_inbound import MatchedDrainInbound
from automatic_picking.application.drain_model import AutomaticPickingDrain, AutomaticPickingDrainRack
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from sqlalchemy import select
from test_drain_completion_postgresql import accepted, clean_closed_root
from test_drain_continuation_postgresql import competing_transactions, install, restart
from test_drain_departure_postgresql import departure_result, departures
from test_drain_inbound_postgresql import publisher
from test_drain_inbound_postgresql import result as inbound_result
from test_drain_progress_postgresql import additional_return, current, decided, requests
from test_drain_replenishment_postgresql import (
    database,
    drainable,
    evaluate,
    exhausted,
    fingerprint,
    independent_move,
    observed,
    pending,
    qualified,
    read,
)
from test_drain_return_postgresql import movement_result
from test_drain_rotation_postgresql import no_batch, rotation_result
from test_drain_wait_postgresql import waiting
from test_return_allocation_postgresql import advance, allocation, arrived
from test_return_allocation_postgresql import result as batch_result
from test_returns_postgresql import processor
from test_source_rack_departure_decision_postgresql import decided as departure_decided
from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

from src.app.execution.models import InboundEvidence, TransportDecisionBinding
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask
from src.app.workline.models import WorkLine
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def ready_result(p, *, rack="OFFLINE-NEXT", faces=("A",)):
    eid = await decided(p, "READY")
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, eid)
        fact.normalized_payload = {
            **fact.normalized_payload,
            "data": {"result": "READY", "racks": [{"rack_id": rack, "rack_face": list(faces)}]},
        }
        fact.payload_digest = canonical_json_digest(fact.normalized_payload)
    assert await processor(p, scan=p.app[1]).process_batch() == 1
    return eid


async def successor(p, *, rack="OFFLINE-NEXT", faces=("A",), original=None):
    if original is None:
        for index in range(len(faces) - 1):
            await additional_return(p, bin_code=f"A{index + 2:09d}", timestamp=2000 + index)
        original = await exhausted(p)
    await advance(p, batch=install(p))
    eid = await ready_result(p, rack=rack, faces=faces)
    return original, await current(p), eid


async def input_for(p, root, rack):
    async with p.sessions.begin() as db:
        if await db.scalar(select(WorkLinePosition).where(WorkLinePosition.position_code == rack)) is None:
            db.add(
                WorkLinePosition(
                    workline_id=p.line.id,
                    workline_code=p.line.line_code,
                    position_code=rack,
                    position_type="RACK_POSITION",
                    position_name="offline",
                    position_role="SMT_RETURN_RACK_POSITION",
                    allowed_rack_kind="RETURN",
                    logic_location_code=rack + "-WORK",
                    capacity=4,
                    enabled=True,
                )
            )
    p.inbound_inputs = {
        rack: MatchedDrainInbound(
            p.line.id,
            root.operation_id,
            root.response_evidence_id,
            rack,
            TransportRackPosition(rack + "-WORK"),
            "A",
            TransportRcsTemplateId.CTU01,
        )
    }


async def actions(p, root, template):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(TransportTask)
                    .join(
                        TransportDecisionBinding,
                        TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                    )
                    .where(
                        TransportDecisionBinding.source_evidence_id == root.response_evidence_id,
                        TransportTask.request_json["rcs_template_id"].as_string() == template,
                    )
                )
            ).all()
        )


async def stage(p, root):
    async with p.sessions() as db:
        return await db.scalar(
            select(AutomaticPickingReturnMotion)
            .where(AutomaticPickingReturnMotion.source_evidence_id == root.response_evidence_id)
            .order_by(AutomaticPickingReturnMotion.id.desc())
        )


async def tick(p):
    p.now = timezone.now_for_db()
    return await advance(p, batch=p.app[0])


async def serve(p, root, *, rack="OFFLINE-NEXT", faces=("A",), fill=False, handoff=True):
    await input_for(p, root, rack)
    await tick(p)
    inbound = await actions(p, root, "CTU01")
    assert len(inbound) == 1
    await inbound_result(p, inbound[0], location=rack + "-WORK")
    await tick(p)
    for index, face in enumerate(faces):
        if fill:
            await tick(p)
            active = await allocation(p)
            assert active.rack_id == rack and active.rack_face == face
            await batch_result(p)
            await processor(p, scan=p.app[1]).process_batch()
            await tick(p)
            await movement_result(p, await allocation(p))
        else:
            await no_batch(p)
        await tick(p)
        if index < len(faces) - 1:
            task = (await actions(p, root, "CTU02"))[-1]
            assert task.request_json["target_face"] == faces[index + 1]
            await rotation_result(p, task)
            await tick(p)
    current_stage = await stage(p, root)
    assert current_stage.rack_face == faces[-1] and current_stage.departure_operation_id is not None
    await departure_decided(p, "READY", operation_id=current_stage.departure_operation_id)
    await tick(p)
    async with p.sessions() as db:
        current_stage = await stage(p, root)
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current_stage.departure_transport_task_id)
        )
    assert task is not None
    if handoff:
        await accepted(p)
        await tick(p)
    return inbound[0], task


async def test_successor_ready_reads_its_own_supply_without_reloading_first_root(drainable):
    p = drainable
    original, next_current, _ = await successor(p)
    before = await fingerprint(p)
    fact = await read(p)
    assert fact is not None
    assert fact.operation_id == next_current.operation_id != original.operation_id
    assert fact.reservation_operation_id == original.operation_id
    assert fact.departure_handoffs == ()
    assert fact.return_ids == tuple(next_current.return_ids)
    assert evaluate(fact).action == "WAIT"
    assert await fingerprint(p) == before


@pytest.mark.parametrize("prior_unknown", [False, True])
async def test_two_ready_rounds_exhaust_to_exactly_one_current_c(drainable, prior_unknown):
    p = drainable
    first, second, _ = await successor(p, faces=("A", "B"))
    if prior_unknown:
        await departure_result(p, (await departures(p))[0], unknown=True)
    inbound, departure = await serve(p, second, faces=("A", "B"))
    third = await current(p)
    assert third.id != second.id and third.operation_id not in {first.operation_id, second.operation_id}
    assert third.started_operation_id == first.operation_id
    assert third.return_ids == third.request_return_ids == second.return_ids
    assert third.response_evidence_id is None and len(await requests(p)) == 3
    assert inbound.request_json["rack_id"] == "OFFLINE-NEXT"
    async with p.sessions() as db:
        assert (await db.get(TransportTask, departure.id)).status == "ACCEPTED"
    await tick(p)
    assert (await current(p)).id == third.id and len(await requests(p)) == 3


@pytest.mark.parametrize("boundary", ["accepted", "unknown", "unpublished"])
async def test_same_rack_waits_for_original_departure_success_not_only_acceptance(drainable, boundary):
    p = drainable
    _, second, _ = await successor(p, rack="OFFLINE-DRAIN")
    original_departure = (await departures(p))[0]
    await input_for(p, second, "OFFLINE-DRAIN")
    if boundary == "unknown":
        await departure_result(p, original_departure, unknown=True)
    elif boundary == "unpublished":
        await departure_result(p, original_departure, publish=False)
    await tick(p)
    assert await actions(p, second, "CTU01") == []
    if boundary == "unknown":
        await tick(p)
        assert await actions(p, second, "CTU01") == []
        return
    if boundary == "unpublished":
        assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
        await processor(p, scan=p.app[1]).process_batch()
    else:
        await departure_result(p, original_departure)
    await tick(p)
    assert len(await actions(p, second, "CTU01")) == 1
    async with p.sessions() as db:
        rows = (await db.scalars(select(AutomaticPickingDrainRack))).all()
        new = next(row for row in rows if row.drain_id == second.id)
        assert new.arrival_evidence_id is None
        binding = await db.get(TransportDecisionBinding, new.binding_id)
        assert binding.source_evidence_id == second.response_evidence_id
        assert binding.client_request_id != original_departure.client_request_id


async def test_due_wait_ready_resumes_supply_and_exhausts_without_reopening_earlier_current(drainable):
    p = drainable
    first, wait, _ = await waiting(p)
    p.now = wait.retry_at
    await advance(p, batch=p.app[0])
    await ready_result(p)
    ready = await current(p)
    await serve(p, ready)
    next_current = await current(p)
    assert next_current.operation_id not in {first.operation_id, wait.operation_id, ready.operation_id}
    assert next_current.started_operation_id == first.operation_id
    assert next_current.return_ids == ready.return_ids and len(await requests(p)) == 4


async def test_normal_close_after_successor_ready_keeps_original_departure_owners(drainable):
    p = drainable
    first, second, _ = await successor(p, faces=("A", "B"))
    original_departure = (await departures(p))[0]
    _, last_departure = await serve(p, second, faces=("A", "B"), fill=True)
    assert await current(p) is None and len(await requests(p)) == 2
    async with p.sessions() as db:
        closed = await db.get(AutomaticPickingDrain, second.id)
        assert closed.returned_ids == second.return_ids and closed.archived_at is not None
    await clean_closed_root(p, first)
    await clean_closed_root(p, second)
    p.positions, p.buffer = {}, None
    fresh = restart(p)
    for task in (original_departure, last_departure):
        await departure_result(p, task)
        p.now = timezone.now_for_db()
        await advance(p, batch=fresh)
    assert await current(p) is None and await requests(p) == []
    async with p.sessions() as db:
        phases = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        assert all(row.archived_at is not None for row in phases)


@pytest.mark.parametrize("fulfilled", [False, True])
async def test_concurrent_final_handoff_creates_one_successor_or_one_normal_close(drainable, fulfilled):
    p = drainable
    _, second, _ = await successor(p)
    await serve(p, second, fill=fulfilled, handoff=False)
    await accepted(p)
    await asyncio.gather(*(tick(p) for _ in range(6)))
    result = await current(p)
    if fulfilled:
        assert result is None and len(await requests(p)) == 2
    else:
        assert result.id != second.id and len(await requests(p)) == 3
        async with p.sessions() as db:
            rows = (await db.scalars(select(AutomaticPickingDrain))).all()
            assert sum(row.archived_at is None for row in rows) == 1


@pytest.mark.parametrize("crash_at", ["inbound", "next_request", "next_current"])
async def test_successor_transport_or_next_current_creation_rolls_back_atomically(drainable, crash_at):
    p = drainable
    _, second, _ = await successor(p)
    if crash_at == "inbound":
        await input_for(p, second, "OFFLINE-NEXT")
        owner, method = p.app[2].inbound.creator, "create_windowed_inbound"
    else:
        await serve(p, second, handoff=False)
        await accepted(p)
        owner = p.app[2].scheduler if crash_at == "next_request" else p.app[2].repository
        method = "create_in_session" if crash_at == "next_request" else "continue_current"
    before = await fingerprint(p)
    call = getattr(owner, method)

    async def crash(*args, **kwargs):
        await call(*args, **kwargs)
        raise RuntimeError("continuous READY crash")

    setattr(owner, method, crash)
    with pytest.raises(RuntimeError, match="continuous READY crash"):
        await tick(p)
    setattr(owner, method, call)
    assert await fingerprint(p) == before
    assert (await current(p)).id == second.id and len(await requests(p)) == 2
    await tick(p)
    if crash_at == "inbound":
        assert len(await actions(p, second, "CTU01")) == 1
    else:
        assert (await current(p)).id != second.id and len(await requests(p)) == 3


@pytest.mark.parametrize("order", ["continuation_first", "owner_first", "owner_rollback"])
async def test_return_success_races_next_ready_handoff_without_losing_responsibility(drainable, order):
    p = drainable
    _, original_allocation = await independent_move(p)
    first = await current(p)
    _, second, _ = await successor(p, original=first)
    assert len(second.return_ids) == 2 and len(second.request_return_ids) == 1
    await serve(p, second, handoff=False)
    await accepted(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        await p.app[2].prepare_return_motion_in_session(db, line)
    await movement_result(p, original_allocation)

    async def owner(db, line):
        await p.app[0].return_batch_flow.advance_in_session(db, line)

    await competing_transactions(p, p.app[2].advance_in_session, owner, order)
    third = await current(p)
    assert third.id != second.id and third.request_return_ids == second.request_return_ids
    if order == "owner_first":
        assert third.return_ids == second.request_return_ids and third.returned_ids == []
    else:
        assert third.return_ids == second.return_ids
        assert third.returned_ids == ([p.return_id] if order == "continuation_first" else [])
    if order == "owner_rollback":
        await tick(p)
        assert (await current(p)).returned_ids == [p.return_id]
    assert len(await requests(p)) == 3


async def test_current_c_and_original_actions_recover_after_both_ready_roots_are_cleaned(drainable):
    p = drainable
    first, second, eid = await successor(p)
    first_departure = (await departures(p))[0]
    _, second_departure = await serve(p, second)
    third = await current(p)
    await clean_closed_root(p, first)
    await clean_closed_root(p, second)
    fresh = restart(p)
    before = await fingerprint(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert (
            await p.app[2].apply_result_in_session(db, line=line, evidence=await db.get(InboundEvidence, eid))
            == "IGNORED"
        )
    assert await fingerprint(p) == before
    for task in (first_departure, second_departure):
        await departure_result(p, task)
        p.now = timezone.now_for_db()
        await advance(p, batch=fresh)
    async with p.sessions.begin() as db:
        rows = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        assert all(row.archived_at is not None for row in rows)
        for row in rows:
            await db.delete(row)
    fresh = restart(p)
    await asyncio.gather(*(advance(p, batch=fresh) for _ in range(3)))
    assert (await current(p)).id == third.id and len(await requests(p)) == 1
