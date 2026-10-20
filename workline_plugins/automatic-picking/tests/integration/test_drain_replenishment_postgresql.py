"""首次 READY 持久事实的只读求值；不创建补架义务或替换 current。"""

import asyncio

import pytest
from sqlalchemy import event, select, text
from test_drain_completion_postgresql import accepted
from test_drain_departure_decision_postgresql import application
from test_drain_departure_postgresql import phase
from test_drain_progress_postgresql import (
    additional_return,
    current,
    database,
    decided,
    drainable,
    observed,
    pending,
    qualified,
)
from test_drain_progress_postgresql import application as drain_application
from test_drain_rotation_postgresql import no_batch
from test_return_allocation_postgresql import LocationReader, advance, allocation, arrived
from test_returns_postgresql import processor
from test_source_rack_departure_decision_postgresql import decided as departure_decided

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.workline.models import WorkLine
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def reader(p):
    from automatic_picking.application.drain_replenishment_reader import FirstDrainReadyReader
    from test_drain_progress_postgresql import BufferReader

    return FirstDrainReadyReader(buffer_reader=BufferReader(p), location_reader=LocationReader(p))


async def read(p, *, instance=None):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        result = await (instance or reader(p)).read_in_session(db, line=line, now=p.now)
        assert not db.new and not db.dirty and not db.deleted
        return result


async def exhausted(p):
    await application(p)
    await no_batch(p)
    await departure_decided(p, "READY")
    await advance(p, batch=p.app[0])
    await accepted(p)
    await advance(p, batch=p.app[0])
    root = await current(p)
    assert root.departure_handoffs and not root.returned_ids
    return root


def evaluate(fact):
    from automatic_picking.drain_replenishment import evaluate_replenishment

    return evaluate_replenishment(fact)


async def fingerprint(p):
    async with p.sessions() as db:
        tables = (
            (await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='wes_biz' ORDER BY tablename")))
            .scalars()
            .all()
        )
        return {
            table: await db.scalar(
                text(
                    f"SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text, '')) "
                    f'FROM wes_biz."{table}" t'
                )
            )
            for table in tables
        }


async def test_first_ready_current_facts_reach_rules_without_persistent_side_effects(drainable):
    p = drainable
    root = await exhausted(p)
    before = await fingerprint(p)
    value = await read(p)
    assert value.operation_id == value.reservation_operation_id == root.operation_id
    assert value.return_ids == (p.return_id,)
    assert value.outcome_operation_id == root.operation_id and value.published
    result = evaluate(value)
    assert result.action == "REQUEST"
    assert result.remaining_return_ids == result.request_return_ids == (p.return_id,)
    assert await fingerprint(p) == before
    assert (await current(p)).id == root.id


@pytest.mark.parametrize("boundary", ["missing", "unknown", "unpublished", "unapplied", "unconsumed", "wait"])
async def test_only_published_applied_and_consumed_first_ready_can_supply_facts(drainable, boundary):
    p = drainable
    if boundary != "missing":
        batch, scan, _ = drain_application(p)
        await advance(p, batch=batch)
        if boundary not in {"unknown"}:
            eid = await decided(p, "WAIT" if boundary == "wait" else "READY", publish=False)
            if boundary not in {"unpublished", "unconsumed"}:
                await processor(p, scan=scan).process_batch()
                await advance(p, batch=batch)
            if boundary == "unconsumed":
                from src.utils.canonical_json import canonical_json_digest

                async with p.sessions.begin() as db:
                    evidence = await db.get(InboundEvidence, eid)
                    evidence.published_at = p.now
                    evidence.decision_digest = canonical_json_digest({"fixture": "published without root binding"})
            if boundary == "unapplied":
                async with p.sessions.begin() as db:
                    (await db.get(InboundEvidence, eid)).apply_status = "PENDING"
    before = await fingerprint(p)
    assert await read(p) is None
    assert await fingerprint(p) == before


@pytest.mark.parametrize(
    "drift", ["payload", "workline", "operation_id", "evidence_operation", "current_evidence", "member_count"]
)
async def test_exact_current_identity_or_payload_drift_fails_closed(drainable, drift):
    from automatic_picking.application.drain_model import AutomaticPickingDrain

    p = drainable
    root = await exhausted(p)
    async with p.sessions.begin() as db:
        saved = await db.get(AutomaticPickingDrain, root.id)
        evidence = await db.get(InboundEvidence, root.response_evidence_id)
        if drift == "payload":
            evidence.normalized_payload = {**evidence.normalized_payload, "timestamp": 9999}
        elif drift == "workline":
            evidence.workline_id = None
        elif drift == "operation_id":
            from test_return_allocation_postgresql import new_uuid7

            evidence.operation_id = new_uuid7()
        elif drift == "evidence_operation":
            evidence.operation = "other.operation@v1"
        elif drift == "current_evidence":
            saved.response_evidence_id = (
                await db.scalar(select(InboundEvidence).where(InboundEvidence.id != root.response_evidence_id))
            ).id
        else:
            saved.return_ids = [*saved.return_ids, 99999]
    before = await fingerprint(p)
    with pytest.raises(ValueError):
        await read(p)
    assert await fingerprint(p) == before


async def test_ready_without_all_handoffs_waits_before_reading_new_positions(drainable):
    p = drainable
    await application(p)
    p.positions = {}
    value = await read(p)
    assert evaluate(value).action == "WAIT"
    assert value.fifo == ()


@pytest.mark.parametrize("boundary", ["unknown_position", "other_position", "unqualified", "unpublished_scan"])
async def test_unknown_or_unqualified_head_is_never_skipped(drainable, boundary):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = drainable
    second = await additional_return(p)
    await exhausted(p)
    if boundary == "unknown_position":
        del p.positions[p.return_id]
    elif boundary == "other_position":
        p.positions[p.return_id] = "OTHER"
    else:
        async with p.sessions.begin() as db:
            row = await db.get(AutomaticPickingReturn, p.return_id)
            if boundary == "unqualified":
                row.ready_evidence_id = None
            else:
                (await db.get(InboundEvidence, row.scan4_evidence_id)).published_at = None
    value = await read(p)
    if boundary == "unpublished_scan":
        assert value is None
    else:
        result = evaluate(value)
        assert result.action == "WAIT" and result.request_return_ids == ()
        assert result.remaining_return_ids == (p.return_id, second)


async def test_later_execution_is_not_added_to_original_request(drainable):
    p = drainable
    root = await exhausted(p)
    later = await additional_return(p, bin_code="A123456789")
    value = await read(p)
    assert later != p.return_id
    assert evaluate(value).request_return_ids == tuple(root.return_ids)
    assert later not in value.return_ids


async def test_restart_and_cleaning_closed_intermediate_motion_preserve_exhaustion(drainable):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_departure_postgresql import departure_result, departures

    p = drainable
    await exhausted(p)
    stage = await phase(p)
    await departure_result(p, (await departures(p))[0])
    await advance(p, batch=p.app[0])
    expected = evaluate(await read(p))
    async with p.sessions.begin() as db:
        old = await db.get(AutomaticPickingReturnMotion, stage.id)
        assert old.archived_at is not None
        await db.delete(old)
    p.app = None
    before = await fingerprint(p)
    fresh = await read(p, instance=reader(p))
    assert evaluate(fresh) == expected
    assert evaluate(fresh).action == "REQUEST"
    assert await fingerprint(p) == before


async def test_returned_positive_progress_survives_cleaning_closed_return_and_allocation(drainable):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from test_drain_return_postgresql import movement_result
    from test_return_allocation_postgresql import result as batch_result

    p = drainable
    second = await additional_return(p)
    await application(p)
    await advance(p, batch=p.app[0])
    await batch_result(p, selected=1)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    await movement_result(p)
    p.now = timezone.now_for_db()
    await advance(p, batch=p.app[0])
    await departure_decided(p, "READY")
    await advance(p, batch=p.app[0])
    await accepted(p)
    await advance(p, batch=p.app[0])
    root = await current(p)
    a = await allocation(p)
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.archived_at is not None
        await db.delete(row)
        old = await db.get(AutomaticPickingReturnAllocation, a.id)
        assert old.settled_at is not None
        await db.delete(old)
    value = await read(p)
    assert value.returned_ids == (p.return_id,) == tuple(root.returned_ids)
    assert evaluate(value).remaining_return_ids == (second,)
    assert evaluate(value).request_return_ids == (second,)
    assert evaluate(value).action == "REQUEST"


async def test_missing_original_member_is_not_inferred_as_returned_or_inflight(drainable):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = drainable
    await exhausted(p)
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingReturn, p.return_id))
    with pytest.raises(ValueError, match="scope"):
        evaluate(await read(p))


async def independent_move(p, *, handoff=True):
    from test_return_allocation_postgresql import application as ordinary_application
    from test_return_allocation_postgresql import result as batch_result

    second = await additional_return(p)
    await exhausted(p)
    p.faces = [(p.face_id, "OFFLINE-RACK", "A")]
    batch, scan = ordinary_application(p)
    await advance(p, batch=batch)
    await batch_result(p, selected=1)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    if handoff:
        await accepted(p)
        await advance(p, batch=batch)
    return second, await allocation(p)


async def test_independent_accepted_bin_move_keeps_responsibility_without_blocking_other_members(drainable):
    from automatic_picking.application.drain_flow import MatchedDrainBuffer
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = drainable
    second, a = await independent_move(p)
    assert a.handed_off_at is not None and a.settled_at is None
    p.buffer = MatchedDrainBuffer("OFFLINE-OUTLET", 1)  # 当前缓冲位只含剩余一箱，不限制整份原责任。
    async with p.sessions.begin() as db:
        closed = await db.get(AutomaticPickingReturn, p.return_id)
        assert closed.archived_at is not None
        await db.delete(closed)
    before = await fingerprint(p)
    value = await read(p)
    assert value.inflight_return_ids == (p.return_id,)
    result = evaluate(value)
    assert result.remaining_return_ids == (p.return_id, second)
    assert result.request_return_ids == (second,)
    assert result.action == "REQUEST"
    assert await fingerprint(p) == before


async def test_unaccepted_allocation_keeps_the_fifo_head_claim_instead_of_inventing_inflight(drainable):
    p = drainable
    second, _ = await independent_move(p, handoff=False)
    value = await read(p)
    assert value.inflight_return_ids == ()
    result = evaluate(value)
    assert result.action == "WAIT" and result.request_return_ids == ()
    assert result.remaining_return_ids == (p.return_id, second)


@pytest.mark.parametrize("drift", ["binding", "member_source", "member_target", "unpublished_allocation"])
async def test_inflight_requires_exact_original_allocation_binding_and_members(drainable, drift):
    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportMember, TransportTask

    p = drainable
    _, a = await independent_move(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == a.transport_task_id)
        )
        if drift == "binding":
            binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
            binding.source_evidence_id = p.face_id
        elif drift == "member_source":
            member.source_json = {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}
        elif drift == "member_target":
            member.target_json = {**member.target_json, "slot_id": "OTHER"}
        else:
            (await db.get(InboundEvidence, a.response_evidence_id)).published_at = None
    with pytest.raises(ValueError):
        await read(p)


async def test_archived_return_alone_is_not_a_positive_handoff(drainable):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = drainable
    await exhausted(p)
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).archived_at = p.now
    with pytest.raises(ValueError, match="scope"):
        evaluate(await read(p))


async def concurrent_owner_read(p, owner, order):
    """以 PostgreSQL 实际阻塞为屏障，读口与原 owner 分别先持有 WorkLine 锁。"""
    held, release = asyncio.Event(), asyncio.Event()
    waiter_pid = asyncio.get_running_loop().create_future()
    blocker_pid = None

    async def transaction(*, reading, first):
        nonlocal blocker_pid
        async with p.sessions() as db:
            async with db.begin():
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                if first:
                    blocker_pid = pid
                else:
                    waiter_pid.set_result(pid)
                line = await db.get(WorkLine, p.line.id, with_for_update=True)
                if first and reading:
                    held.set()
                    await release.wait()
                if reading:
                    statements = []

                    def record(connection, cursor, statement, parameters, context, executemany):
                        statements.append(statement)

                    connection = (await db.connection()).sync_connection
                    event.listen(connection, "before_cursor_execute", record)
                    try:
                        fact = await reader(p).read_in_session(db, line=line, now=p.now)
                        result = evaluate(fact) if fact is not None else None
                        assert not db.new and not db.dirty and not db.deleted
                    finally:
                        event.remove(connection, "before_cursor_execute", record)
                    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
                    value = fact, result
                else:
                    await owner(db, line)
                    value = None
                if first and not reading:
                    held.set()
                    await release.wait()
                if first and order == "owner_rollback":
                    await db.rollback()
                return value

    first = asyncio.create_task(transaction(reading=order == "read_first", first=True))
    second = None
    try:
        await asyncio.wait_for(held.wait(), 10)
        second = asyncio.create_task(transaction(reading=order != "read_first", first=False))
        pid = await asyncio.wait_for(waiter_pid, 10)

        async def blocked():
            async with p.sessions() as db:
                while not await db.scalar(
                    text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
                    {"blocker": blocker_pid, "waiter": pid},
                ):
                    if second.done():
                        await second
                        pytest.fail("second transaction bypassed the WorkLine lock")
                    await asyncio.sleep(0.01)

        await asyncio.wait_for(blocked(), 10)
        release.set()
        values = await asyncio.wait_for(asyncio.gather(first, second), 10)
        return values[0 if order == "read_first" else 1]
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)


@pytest.mark.parametrize("order", ["read_first", "owner_first", "owner_rollback"])
@pytest.mark.parametrize("progress", ["returned", "bin_move_handoff", "normal_close"])
async def test_first_ready_read_serializes_with_existing_owner_progress(drainable, progress, order):
    from automatic_picking.application.drain_model import AutomaticPickingDrain
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_completion_postgresql import departing
    from test_drain_return_postgresql import created, movement_result
    from test_return_allocation_postgresql import application as ordinary_application

    from src.app.transport.models import TransportTask

    p = drainable
    if progress == "returned":
        a = await created(p)
        await movement_result(p, a)
        owner = p.app[0].return_batch_flow.advance_in_session
    elif progress == "bin_move_handoff":
        second, a = await independent_move(p, handoff=False)
        await accepted(p)
        p.faces = []  # 此次只恢复已创建的独立 BIN_MOVE，不准入新分配。
        owner = ordinary_application(p)[0].return_batch_flow.advance_in_session
    else:
        root, task = await departing(p)
        stage = await phase(p)
        await accepted(p)
        owner = p.app[0].advance_completed_in_session

    root = await current(p)
    before = await read(p)
    assert before.return_ids == tuple(root.return_ids)
    assert before.operation_id == before.reservation_operation_id == root.operation_id
    if progress == "returned":
        assert before.returned_ids == ()
        assert evaluate(before).remaining_return_ids == (p.return_id,)
    elif progress == "bin_move_handoff":
        assert before.inflight_return_ids == ()
        assert evaluate(before).action == "WAIT"
    else:
        assert before.returned_ids == before.return_ids
        assert not before.departure_handoffs
        assert evaluate(before).action == "WAIT"
    async with p.sessions() as db:
        confirmations = set((await db.scalars(select(WmsConfirmation.id))).all())

    fact, result = await concurrent_owner_read(p, owner, order)
    after = await read(p)  # 新会话、新 reader：没有缓存求值或独立恢复 owner。
    if order == "owner_first":
        assert fact == after
    else:
        assert fact == before
    if fact is not None:
        assert result == evaluate(fact)
        assert fact.operation_id == fact.reservation_operation_id == root.operation_id
    else:
        assert result is None

    if order == "owner_rollback":
        assert after == before
        async with p.sessions.begin() as db:
            line = await db.get(WorkLine, p.line.id, with_for_update=True)
            await owner(db, line)
        after = await read(p)

    async with p.sessions() as db:
        saved = await db.get(AutomaticPickingDrain, root.id)
        assert saved.operation_id == root.operation_id
        assert set((await db.scalars(select(WmsConfirmation.id))).all()) == confirmations
        if progress == "normal_close":
            assert after is None and await current(p) is None
            assert saved.archived_at is not None and saved.returned_ids == saved.return_ids
            assert saved.departure_handoffs == {stage.rack_id: task.transport_task_id}
            assert (await db.get(TransportTask, task.id)).status == "ACCEPTED"
            motion = await db.get(AutomaticPickingReturnMotion, stage.id)
            assert motion.archived_at is None and motion.departure_transport_task_id == task.transport_task_id
        else:
            assert saved.archived_at is None and (await current(p)).id == root.id
            saved_allocation = await allocation(p)
            if progress == "returned":
                assert after.returned_ids == (p.return_id,)
                assert evaluate(after).remaining_return_ids == ()
                assert evaluate(after).action == "WAIT" and evaluate(after).request_return_ids == ()
                assert saved_allocation.handed_off_at is not None and saved_allocation.settled_at is not None
            else:
                assert after.returned_ids == () and after.inflight_return_ids == (p.return_id,)
                assert evaluate(after).remaining_return_ids == (p.return_id, second)
                assert evaluate(after).request_return_ids == (second,)
                assert evaluate(after).action == "REQUEST"
                assert saved_allocation.handed_off_at is not None and saved_allocation.settled_at is None

    stable = await fingerprint(p)
    assert await read(p) == after
    assert await fingerprint(p) == stable
