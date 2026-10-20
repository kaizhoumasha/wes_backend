"""补架 WAIT 的持久续问与责任退出；不执行连续 READY 供架。"""

import asyncio
from datetime import timedelta

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
from sqlalchemy import select
from test_drain_completion_postgresql import accepted, clean_closed_root
from test_drain_continuation_postgresql import competing_transactions, install, restart
from test_drain_progress_postgresql import current, decided, queued, requests
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
from test_return_allocation_postgresql import advance, allocation, arrived
from test_return_allocation_postgresql import application as ordinary_application
from test_return_allocation_postgresql import result as batch_result
from test_returns_postgresql import processor

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def waiting(p, *, inflight=False):
    if inflight:
        await independent_move(p)
        original = await current(p)
    else:
        original = await exhausted(p)
    await advance(p, batch=install(p))
    eid = await decided(p, "WAIT")
    assert await processor(p, scan=p.app[1]).process_batch() == 1
    return original, await current(p), eid


async def test_due_replenishment_wait_hands_off_once_without_initial_prepare(drainable):
    p = drainable
    original, wait, _ = await waiting(p)
    await queued(p)
    p.now = wait.retry_at - timedelta(microseconds=1)
    await advance(p, batch=p.app[0])
    assert (await current(p)).id == wait.id and len(await requests(p)) == 2
    p.now = wait.retry_at
    await advance(p, batch=p.app[0])
    successor = await current(p)
    assert successor.id != wait.id
    assert successor.started_operation_id == original.operation_id
    assert successor.return_ids == successor.request_return_ids == [p.return_id]
    assert successor.response_evidence_id is None and successor.retry_at is None
    assert len(await requests(p)) == 3
    await advance(p, batch=p.app[0])
    assert (await current(p)).id == successor.id and len(await requests(p)) == 3


async def test_wait_reader_is_readonly_and_preserves_fixed_deadline(drainable):
    p = drainable
    original, wait, _ = await waiting(p)
    before = await fingerprint(p)
    for now in (p.now, wait.retry_at - timedelta(microseconds=1), wait.retry_at):
        p.now = now
        fact = await read(p)
        assert fact.reservation_operation_id == original.operation_id
        assert fact.operation_id == wait.operation_id and fact.return_ids == (p.return_id,)
        assert evaluate(fact).action == ("REQUEST" if now == wait.retry_at else "WAIT")
        assert (await current(p)).retry_at == wait.retry_at
    assert await fingerprint(p) == before


async def test_same_wait_result_replay_does_not_restart_its_deadline(drainable):
    p = drainable
    _, wait, eid = await waiting(p)
    p.now = wait.retry_at - timedelta(milliseconds=1)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert (
            await p.app[2].apply_result_in_session(db, line=line, evidence=await db.get(InboundEvidence, eid))
            == "DRAIN_DECIDED"
        )
    assert (await current(p)).retry_at == wait.retry_at


async def test_concurrent_due_scans_and_repeated_waits_keep_one_current(drainable):
    p = drainable
    original, wait, _ = await waiting(p)
    for expected_count in (3, 4):
        p.now = wait.retry_at
        await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(6)))
        new = await current(p)
        assert new.id != wait.id and new.started_operation_id == original.operation_id
        assert new.return_ids == new.request_return_ids == [p.return_id]
        assert len(await requests(p)) == expected_count
        await decided(p)
        await processor(p, scan=p.app[1]).process_batch()
        wait = await current(p)
        assert wait.retry_at == p.now + timedelta(seconds=1)
        assert await advance(p, batch=p.app[0]) == 0


@pytest.mark.parametrize("crash_at", ["request", "old_exit", "new_current", "allocation_owner"])
async def test_due_wait_atomic_handoff_rolls_back_at_every_boundary(drainable, crash_at):
    p = drainable
    _, wait, _ = await waiting(p, inflight=True)
    a = await allocation(p)
    p.now = wait.retry_at
    before = await fingerprint(p)
    create = p.app[2].scheduler.create_in_session

    async def crash_request(*args, **kwargs):
        result = await create(*args, **kwargs)
        if crash_at == "request":
            raise RuntimeError("wait handoff crash")
        return result

    p.app[2].scheduler.create_in_session = crash_request
    async with p.sessions() as db:
        flush = db.flush

        async def crash_flush(*args, **kwargs):
            await flush(*args, **kwargs)
            old = await db.get(AutomaticPickingDrain, wait.id)
            if old.archived_at is None:
                return
            new = await db.scalar(select(AutomaticPickingDrain).where(AutomaticPickingDrain.archived_at.is_(None)))
            owned = await db.get(AutomaticPickingReturnAllocation, a.id)
            if (
                (crash_at == "old_exit" and new is None)
                or (crash_at == "new_current" and new is not None and owned.drain_id == wait.id)
                or (crash_at == "allocation_owner" and new is not None and owned.drain_id == new.id)
            ):
                raise RuntimeError("wait handoff crash")

        db.flush = crash_flush
        with pytest.raises(RuntimeError, match="wait handoff crash"):
            async with db.begin():
                line = await db.get(WorkLine, p.line.id, with_for_update=True)
                await p.app[0].advance_completed_in_session(db, line)
    p.app[2].scheduler.create_in_session = create
    assert await fingerprint(p) == before
    await advance(p, batch=p.app[0])
    assert (await current(p)).id != wait.id and len(await requests(p)) == 3


@pytest.mark.parametrize("order", ["continuation_first", "owner_first", "owner_rollback"])
async def test_return_fact_races_wait_renewal_without_losing_original_owner(drainable, order):
    p = drainable
    _, wait, _ = await waiting(p, inflight=True)
    original = await allocation(p)
    await movement_result(p, original)
    p.now = wait.retry_at

    async def owner(db, line):
        await p.app[0].return_batch_flow.advance_in_session(db, line)

    await competing_transactions(p, p.app[2].advance_in_session, owner, order)
    new = await current(p)
    assert new.id != wait.id and new.request_return_ids == wait.request_return_ids
    if order == "owner_first":
        assert new.return_ids == wait.request_return_ids and new.returned_ids == []
    else:
        assert new.return_ids == wait.return_ids
        assert new.returned_ids == ([p.return_id] if order == "continuation_first" else [])
    if order == "owner_rollback":
        await advance(p, batch=p.app[0])
        assert (await current(p)).returned_ids == [p.return_id]
    assert len(await requests(p)) == 3


async def ordinary_move(p, *, rack="OFFLINE-RACK"):
    p.faces = [(p.face_id, rack, "A")]
    batch, scan = ordinary_application(p)
    await advance(p, batch=batch)
    await batch_result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    return await allocation(p), batch


async def test_all_remaining_members_in_independent_moves_never_create_zero_request(drainable):
    p = drainable
    _, wait, _ = await waiting(p, inflight=True)
    first = await allocation(p)
    second, ordinary = await ordinary_move(p, rack="OFFLINE-INDEPENDENT")
    await accepted(p)
    await advance(p, batch=ordinary)
    p.positions = {}
    p.now = wait.retry_at
    await advance(p, batch=p.app[0])
    assert (await current(p)).id == wait.id and len(await requests(p)) == 2
    for a in (first, second):
        await movement_result(p, a)
        await advance(p, batch=ordinary)
    assert set((await current(p)).returned_ids) == set(wait.return_ids)
    await advance(p, batch=p.app[0])
    assert await current(p) is None and len(await requests(p)) == 2


@pytest.mark.parametrize("inactive", [False, True])
async def test_ordinary_success_closes_zero_responsibility_wait_without_new_request(drainable, inactive):
    from test_drain_departure_postgresql import departure_result, departures

    p = drainable
    old, wait, _ = await waiting(p)
    departure = (await departures(p))[0]
    a, ordinary = await ordinary_move(p)
    assert a.drain_id == wait.id
    await movement_result(p, a)
    await advance(p, batch=ordinary)
    assert (await current(p)).returned_ids == [p.return_id]
    if inactive:
        async with p.sessions.begin() as db:
            (await db.get(WorkLine, p.line.id)).is_active = False
    p.buffer, p.positions = None, {}
    # 尚未到期也可结束已清零责任；原离场动作仍由原 Motion 恢复。
    await advance(p, batch=p.app[0])
    assert await current(p) is None and len(await requests(p)) == 2
    await clean_closed_root(p, old)
    await clean_closed_root(p, wait)
    fresh = restart(p)
    await departure_result(p, departure)
    await advance(p, batch=fresh)
    assert await current(p) is None


async def test_zero_responsibility_exit_rolls_back_and_recovers(drainable):
    p = drainable
    _, wait, _ = await waiting(p)
    a, ordinary = await ordinary_move(p)
    await movement_result(p, a)
    await advance(p, batch=ordinary)
    before = await fingerprint(p)
    async with p.sessions() as db:
        flush = db.flush

        async def crash(*args, **kwargs):
            await flush(*args, **kwargs)
            if (await db.get(AutomaticPickingDrain, wait.id)).archived_at is not None:
                raise RuntimeError("wait exit crash")

        db.flush = crash
        with pytest.raises(RuntimeError, match="wait exit crash"):
            async with db.begin():
                await p.app[2].advance_in_session(db, await db.get(WorkLine, p.line.id, with_for_update=True))
    assert await fingerprint(p) == before
    await advance(p, batch=restart(p))
    assert await current(p) is None and len(await requests(p)) == 2


@pytest.mark.parametrize("boundary", ["unpublished", "unapplied", "delivery_unknown", "position_unknown"])
async def test_unresolved_request_or_head_keeps_original_identity(drainable, boundary):
    p = drainable
    _, wait, eid = await waiting(p)
    p.now = wait.retry_at
    async with p.sessions.begin() as db:
        evidence = await db.get(InboundEvidence, eid)
        if boundary == "unpublished":
            evidence.published_at = None
        elif boundary == "unapplied":
            evidence.apply_status = "PENDING"
        elif boundary == "delivery_unknown":
            request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == wait.operation_id))
            request.status = "RECONCILING"
        else:
            p.positions = {}
    before = await fingerprint(p)
    await advance(p, batch=p.app[0])
    assert (await current(p)).id == wait.id and len(await requests(p)) == 2
    assert await fingerprint(p) == before


@pytest.mark.parametrize("drift", ["retry_at", "response_evidence_id", "request_return_ids"])
async def test_wait_reader_rejects_frozen_current_identity_drift(drainable, drift):
    p = drainable
    old, wait, _ = await waiting(p)
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingDrain, wait.id)
        if drift == "retry_at":
            row.retry_at += timedelta(milliseconds=1)
        elif drift == "response_evidence_id":
            row.response_evidence_id = old.response_evidence_id
        else:
            row.request_return_ids = []
    before = await fingerprint(p)
    with pytest.raises(ValueError):
        await read(p)
    assert await fingerprint(p) == before


async def test_restart_cleaned_history_and_old_wait_replay_use_only_current(drainable):
    p = drainable
    old, wait, eid = await waiting(p)
    await clean_closed_root(p, old)
    fresh = restart(p)
    await advance(p, batch=fresh)
    assert (await current(p)).retry_at == wait.retry_at
    p.now = wait.retry_at
    await advance(p, batch=fresh)
    next_current = await current(p)
    await clean_closed_root(p, wait)
    fresh = restart(p)
    before = await fingerprint(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert (
            await p.app[2].apply_result_in_session(db, line=line, evidence=await db.get(InboundEvidence, eid))
            == "IGNORED"
        )
    await advance(p, batch=fresh)
    assert (await current(p)).id == next_current.id
    assert await fingerprint(p) == before
    await decided(p)
    await processor(p, scan=p.app[1]).process_batch()
    assert (await current(p)).retry_at == p.now + timedelta(seconds=1)


async def test_wait_renewal_ready_is_saved_without_enabling_continuous_ready_reader(drainable):
    p = drainable
    _, wait, _ = await waiting(p)
    p.now = wait.retry_at
    await advance(p, batch=p.app[0])
    eid = await decided(p, "READY")
    await processor(p, scan=p.app[1]).process_batch()
    saved = await current(p)
    assert saved.response_evidence_id == eid and saved.retry_at is None
    before = await fingerprint(p)
    assert await read(p) is None
    assert await fingerprint(p) == before
