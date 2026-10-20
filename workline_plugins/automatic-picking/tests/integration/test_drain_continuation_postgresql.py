"""现有 drain DTO 的单次 current 原子接续；只使用显式离线权威事实。"""

import asyncio
from datetime import timedelta

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from sqlalchemy import select, text
from test_drain_completion_postgresql import clean_closed_root
from test_drain_progress_postgresql import current, decided, queued, requests
from test_drain_replenishment_postgresql import (
    database,
    drainable,
    exhausted,
    fingerprint,
    independent_move,
    observed,
    pending,
    qualified,
    reader,
)
from test_drain_return_postgresql import movement_result
from test_return_allocation_postgresql import advance, arrived
from test_returns_postgresql import processor

from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def install(p):
    # 与既有离线入口相同，只显式关联业务读口，不进入部署装配。
    p.app[2].replenishment_reader = reader(p)
    p.faces = []
    return p.app[0]


def restart(p):
    from test_drain_progress_postgresql import application
    from test_return_allocation_postgresql import application as ordinary_application

    old = p.app[2].inbound
    batch, scan, flow = application(p)
    batch.return_batch_flow = ordinary_application(p)[0].return_batch_flow
    scan.return_batch_flow = batch.return_batch_flow
    flow.inbound = type(old)(old.creator, old.reader)
    flow.inbound.rotation = type(old.rotation)(batch.return_batch_flow, old.creator, clock=lambda: p.now)
    flow.inbound.departure = type(old.departure)(
        flow.inbound.rotation, scheduler=old.departure.scheduler, clock=lambda: p.now
    )
    p.app = batch, scan, flow
    return install(p)


async def test_exhausted_ready_hands_request_and_remaining_scope_to_one_new_current(drainable):
    p = drainable
    old = await exhausted(p)
    batch = install(p)
    await advance(p, batch=batch)
    new = await current(p)
    assert new.id != old.id
    assert new.operation_id != old.operation_id
    assert new.return_ids == new.request_return_ids == [p.return_id]
    assert new.started_operation_id == old.operation_id
    assert new.response_evidence_id is None and new.retry_at is None
    assert new.returned_ids == [] and new.departure_handoffs == {}
    async with p.sessions() as db:
        saved = await db.get(AutomaticPickingDrain, old.id)
        assert saved.archived_at is not None
        assert saved.returned_ids == [] and saved.operation_id == old.operation_id
        assert saved.departure_handoffs == old.departure_handoffs
    saved_requests = await requests(p)
    assert len(saved_requests) == 2
    request = next(r for r in saved_requests if r.operation_id == new.operation_id)
    assert request.workline_id == p.line.id and request.picking_task_id is None
    assert request.request_payload["data"] == {"workline_code": p.line.line_code, "required_slot_count": 1}
    await asyncio.gather(*(advance(p, batch=batch) for _ in range(4)))
    assert (await current(p)).id == new.id and len(await requests(p)) == 2


async def test_independent_inflight_owner_moves_without_changing_original_action(drainable):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    p = drainable
    second, original = await independent_move(p)
    old = await current(p)
    await advance(p, batch=install(p))
    new = await current(p)
    assert new.id != old.id
    assert new.return_ids == [p.return_id, second]
    assert new.request_return_ids == [second]
    async with p.sessions() as db:
        saved = await db.get(AutomaticPickingReturnAllocation, original.id)
        assert saved.drain_id == new.id
        for field in ("operation_id", "transport_task_id", "response_evidence_id", "handed_off_at", "return_ids"):
            assert getattr(saved, field) == getattr(original, field)
    request = next(r for r in await requests(p) if r.operation_id == new.operation_id)
    assert request.request_payload["data"]["required_slot_count"] == 1


async def test_concurrent_scanners_create_only_one_successor(drainable):
    p = drainable
    old = await exhausted(p)
    batch = install(p)
    await asyncio.gather(*(advance(p, batch=batch) for _ in range(6)))
    new = await current(p)
    assert new.id != old.id and len(await requests(p)) == 2
    async with p.sessions() as db:
        roots = (await db.scalars(select(AutomaticPickingDrain))).all()
        assert len(roots) == 2
        assert sum(r.archived_at is None for r in roots) == 1


@pytest.mark.parametrize("crash_at", ["request", "old_exit", "new_current", "allocation_owner"])
async def test_every_handoff_crash_rolls_back_the_whole_request_and_owner(drainable, crash_at):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    p = drainable
    second, a = await independent_move(p)
    old = await current(p)
    batch = install(p)
    before = await fingerprint(p)
    create = p.app[2].scheduler.create_in_session

    async def crash_request(*args, **kwargs):
        value = await create(*args, **kwargs)
        if crash_at == "request":
            raise RuntimeError("handoff crash")
        return value

    p.app[2].scheduler.create_in_session = crash_request
    async with p.sessions() as db:
        flush = db.flush

        async def crash_flush(*args, **kwargs):
            await flush(*args, **kwargs)
            root = await db.get(AutomaticPickingDrain, old.id)
            if root.archived_at is None:
                return
            new = await db.scalar(select(AutomaticPickingDrain).where(AutomaticPickingDrain.archived_at.is_(None)))
            saved_allocation = await db.get(AutomaticPickingReturnAllocation, a.id)
            if (
                (crash_at == "old_exit" and new is None)
                or (crash_at == "new_current" and new is not None and saved_allocation.drain_id == old.id)
                or (crash_at == "allocation_owner" and new is not None and saved_allocation.drain_id == new.id)
            ):
                raise RuntimeError("handoff crash")

        db.flush = crash_flush
        with pytest.raises(RuntimeError, match="handoff crash"):
            async with db.begin():
                line = await db.get(WorkLine, p.line.id, with_for_update=True)
                await batch.advance_completed_in_session(db, line)
    p.app[2].scheduler.create_in_session = create
    assert await fingerprint(p) == before
    assert (await current(p)).id == old.id and len(await requests(p)) == 1
    await advance(p, batch=batch)
    new = await current(p)
    assert new.id != old.id and new.return_ids == [p.return_id, second]


async def competing_transactions(p, continuation, owner, order):
    """复用 WorkLine 锁纪律，以 pg_blocking_pids 验证实际竞争，不靠延时猜顺序。"""
    held, release = asyncio.Event(), asyncio.Event()
    waiter_pid = asyncio.get_running_loop().create_future()
    blocker = None

    async def run(action, first):
        nonlocal blocker
        async with p.sessions.begin() as db:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            if first:
                blocker = pid
            else:
                waiter_pid.set_result(pid)
            line = await db.get(WorkLine, p.line.id, with_for_update=True)
            await action(db, line)
            if first:
                held.set()
                await release.wait()
                if order == "owner_rollback":
                    await db.rollback()

    first_action, second_action = (continuation, owner) if order == "continuation_first" else (owner, continuation)
    first = asyncio.create_task(run(first_action, True))
    second = None
    try:
        await asyncio.wait_for(held.wait(), 10)
        second = asyncio.create_task(run(second_action, False))
        pid = await asyncio.wait_for(waiter_pid, 10)

        async def blocked():
            async with p.sessions() as db:
                while not await db.scalar(
                    text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"), {"blocker": blocker, "waiter": pid}
                ):
                    if second.done():
                        await second
                        pytest.fail("owner bypassed WorkLine lock")
                    await asyncio.sleep(0.01)

        await asyncio.wait_for(blocked(), 10)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 10)
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)


@pytest.mark.parametrize("order", ["continuation_first", "owner_first", "owner_rollback"])
async def test_original_return_fact_has_one_owner_across_current_handoff(drainable, order):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    p = drainable
    second, original = await independent_move(p)
    old = await current(p)
    install(p)
    await movement_result(p, original)
    flow = p.app[2]

    async def owner(db, line):
        await p.app[0].return_batch_flow.advance_in_session(db, line)

    await competing_transactions(p, flow.advance_in_session, owner, order)
    new = await current(p)
    assert new.id != old.id and new.request_return_ids == [second]
    if order == "owner_first":
        assert new.return_ids == [second] and new.returned_ids == []
    else:
        assert new.return_ids == [p.return_id, second]
        assert new.returned_ids == ([p.return_id] if order == "continuation_first" else [])
    if order == "owner_rollback":
        async with p.sessions.begin() as db:
            await owner(db, await db.get(WorkLine, p.line.id, with_for_update=True))
        assert (await current(p)).returned_ids == [p.return_id]
    async with p.sessions() as db:
        allocation = await db.get(AutomaticPickingReturnAllocation, original.id)
        assert allocation.settled_at is not None
        if order != "owner_first":
            assert allocation.drain_id == new.id
    assert len(await requests(p)) == 2
    await advance(p, batch=p.app[0])
    assert (await current(p)).id == new.id and len(await requests(p)) == 2


async def test_successor_and_original_departure_recover_without_old_root_or_executing_task(drainable):
    from test_drain_departure_postgresql import departure_result, departures

    p = drainable
    old = await exhausted(p)
    original_departure = (await departures(p))[0]
    await advance(p, batch=install(p))
    new = await current(p)
    assert new.id != old.id
    await clean_closed_root(p, old)
    # 新实例丢弃所有内存游标；当前请求和原未终态 CTU03 各自恢复。
    fresh = restart(p)
    await asyncio.gather(*(advance(p, batch=fresh) for _ in range(3)))
    assert (await current(p)).id == new.id and len(await requests(p)) == 1
    async with p.sessions() as db:
        from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

        from src.app.transport.models import TransportTask

        assert await db.get(AutomaticPickingDrain, old.id) is None
        assert (await db.get(TransportTask, original_departure.id)).status == "ACCEPTED"
        stage = await db.scalar(select(AutomaticPickingReturnMotion))
        assert stage.departure_transport_task_id == original_departure.transport_task_id
        assert stage.archived_at is None
    await departure_result(p, original_departure)
    await advance(p, batch=fresh)
    async with p.sessions() as db:
        assert (await db.get(TransportTask, original_departure.id)).status == "SUCCEEDED"
        assert (await db.get(AutomaticPickingReturnMotion, stage.id)).archived_at is not None
    assert (await current(p)).id == new.id


async def test_inflight_success_reaches_successor_after_old_root_cleanup(drainable):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    p = drainable
    second, a = await independent_move(p)
    old = await current(p)
    await advance(p, batch=install(p))
    new = await current(p)
    assert new.id != old.id
    await clean_closed_root(p, old)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnAllocation, a.id)).drain_id == new.id
    fresh = restart(p)
    await movement_result(p, a)
    await advance(p, batch=fresh)
    assert (await current(p)).returned_ids == [p.return_id]
    assert (await current(p)).return_ids == [p.return_id, second]


@pytest.mark.parametrize("outcome", ["WAIT", "READY"])
async def test_successor_result_uses_request_scope_not_inflight_responsibility(drainable, outcome):
    p = drainable
    second, _ = await independent_move(p)
    await advance(p, batch=install(p))
    new = await current(p)
    assert new.return_ids == [p.return_id, second] and new.request_return_ids == [second]
    eid = await decided(p, outcome)
    assert await processor(p, scan=p.app[1]).process_batch() == 1
    saved = await current(p)
    assert saved.response_evidence_id == eid and saved.started_operation_id != saved.operation_id
    # 到期前不得进入初始 prepare；到期续问由 WAIT 事务测试拥有。
    await queued(p)
    p.now = saved.retry_at - timedelta(microseconds=1) if outcome == "WAIT" else p.now + timedelta(seconds=2)
    p.app[2].inbound = None
    await advance(p, batch=p.app[0])
    assert (await current(p)).id == new.id and len(await requests(p)) == 2


async def test_existing_ready_upgrade_preserves_scope_and_acquires_only_typed_start_fact(drainable, database):
    from sqlalchemy import inspect

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    before = await exhausted(p)
    url, sessions = database
    await sessions.kw["bind"].dispose()
    run_alembic("downgrade", "77b256c269b3", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    await sessions.kw["bind"].dispose()
    saved = await current(p)
    assert saved.return_ids == saved.request_return_ids == before.return_ids
    assert saved.started_operation_id is None
    assert saved.departure_handoffs == before.departure_handoffs
    assert saved.response_evidence_id == before.response_evidence_id and saved.archived_at is None
    await advance(p, batch=p.app[0])
    assert (await current(p)).started_operation_id == before.operation_id
    assert len(await requests(p)) == 1
    async with p.sessions() as db:
        columns = await (await db.connection()).run_sync(
            lambda c: inspect(c).get_columns("automatic_picking_drains", schema="wes_biz")
        )
        assert {c["name"] for c in columns} == set(AutomaticPickingDrain.__table__.columns.keys())


async def test_downgrade_cannot_discard_successor_responsibility(drainable, database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    await independent_move(p)
    await advance(p, batch=install(p))
    new = await current(p)
    with pytest.raises(subprocess.CalledProcessError):
        run_alembic("downgrade", "77b256c269b3", database_url=database[0])
    assert (await current(p)).id == new.id


@pytest.mark.parametrize("cleanup", [False, True])
async def test_old_ready_replay_cannot_reopen_or_replace_successor(drainable, cleanup):
    from src.app.execution.models import InboundEvidence

    p = drainable
    old = await exhausted(p)
    await advance(p, batch=install(p))
    new = await current(p)
    if cleanup:
        await clean_closed_root(p, old)
    before = await fingerprint(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        evidence = await db.get(InboundEvidence, old.response_evidence_id)
        assert await p.app[2].apply_result_in_session(db, line=line, evidence=evidence) == "IGNORED"
    assert await fingerprint(p) == before
    assert (await current(p)).id == new.id
