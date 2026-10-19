"""drain 只决定与冻结交接；真实 PostgreSQL，位置/容量为显式离线输入。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.drain_flow import AutomaticPickingDrainFlow, MatchedDrainBuffer
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.drain_repository import DrainRepository
from automatic_picking.application.work_repository import WorkRepository
from automatic_picking.prepare_policy import AutomaticPickingPreparePolicy
from sqlalchemy import delete, func, select
from test_return_allocation_postgresql import LocationReader, advance, arrived, database, observed, pending, qualified
from test_return_directions_postgresql import application as direction_application
from test_returns_postgresql import processor

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator
from src.app.wms_integration.return_buffer_drain import (
    ReturnBufferDrainOwnerService,
    ReturnBufferDrainResultReader,
    ReturnBufferDrainScheduler,
)
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from tests.integration.wms_adapter.outbound_picking.confirmation_support import seed_picking_owner

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
OPERATION = "workline.return_buffer.drain_rack_decide@v1"


class BufferReader:
    def __init__(self, p):
        self.p = p

    async def read_in_session(self, db, *, workline_id):
        return self.p.buffer


def application(p):
    _, batch, scan = direction_application(p)
    prepare = PickingTaskPrepareCoordinator(
        p.sessions, policy=AutomaticPickingPreparePolicy(), task_queue_gateway=object()
    )
    flow = AutomaticPickingDrainFlow(
        DrainRepository(),
        prepare,
        ReturnBufferDrainScheduler(WmsConfirmationLifecycleService(workline_owner=ReturnBufferDrainOwnerService())),
        ReturnBufferDrainResultReader(),
        BufferReader(p),
        LocationReader(p),
        clock=lambda: p.now,
    )
    batch.drain_flow = flow
    return batch, scan, flow


@pytest_asyncio.fixture
async def drainable(qualified):
    p = qualified
    p.buffer = MatchedDrainBuffer("OFFLINE-OUTLET", 4)
    async with p.sessions.begin() as db:
        task = await db.get(PickingTask, p.task.id)
        task.status = "EXECUTION_COMPLETED"
        line = await db.get(WorkLine, p.line.id)
        line.run_mode, line.flow_mode = "AUTO", None
    return p


async def requests(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == OPERATION).order_by(WmsConfirmation.id)
                )
            ).all()
        )


async def current(p):
    async with p.sessions() as db:
        return await DrainRepository().current(db, p.line.id)


async def decided(p, result="WAIT", *, operation_id=None, publish=False):
    operation_id = operation_id or (await current(p)).operation_id
    data = (
        {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1000}
        if result == "WAIT"
        else {"result": "READY", "racks": [{"rack_id": "OFFLINE-DRAIN", "rack_face": ["A"]}]}
    )
    payload = {"operation_id": operation_id, "code": "DECIDED", "timestamp": 1000, "data": data}
    async with p.sessions.begin() as db:
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
        fact = InboundEvidence(
            kind="WMS_RESULT",
            operation=OPERATION,
            operation_id=operation_id,
            workline_id=p.line.id,
            source_identity="drain-result:" + operation_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now if publish else None,
            decision_digest=canonical_json_digest({"fixture": "published drain"}) if publish else None,
        )
        db.add(fact)
        await db.flush()
        request.status, request.completed_at, request.response_result = "COMPLETED", p.now, result
        request.response_evidence_id = fact.id
        return fact.id


async def queued(p):
    async with p.sessions.begin() as db:
        task, _ = await seed_picking_owner(
            db,
            operation_id=new_uuid7(),
            issued_operation_id=new_uuid7(),
            task_key="NEXT-" + new_uuid7(),
            now=p.now,
            status="QUEUED",
        )
        task.workline_id, task.task_type = p.line.id, "AUTO"
        await db.flush()
        return task.id


async def test_request_and_ready_freeze_original_members_and_recover_without_task(drainable):
    p = drainable
    batch, scan, _ = application(p)
    assert await advance(p, batch=batch) == 1
    request = (await requests(p))[0]
    frozen = await current(p)
    assert request.workline_id == p.line.id and request.picking_task_id is None
    assert request.request_payload["data"] == {"workline_code": p.line.line_code, "required_slot_count": 1}
    assert frozen.return_ids == [p.return_id]
    eid = await decided(p, "READY")
    assert await processor(p, scan=scan).process_batch() == 1
    assert (await current(p)).response_evidence_id == eid
    await queued(p)
    p.positions = {}
    fresh, _, _ = application(p)
    assert await advance(p, batch=fresh) == 0
    assert len(await requests(p)) == 1 and (await current(p)).return_ids == [p.return_id]
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 0


async def test_wait_receipt_deadline_and_atomic_new_identity_recover(drainable):
    p = drainable
    batch, scan, _ = application(p)
    await advance(p, batch=batch)
    old = (await current(p)).operation_id
    received = p.now
    eid = await decided(p)
    p.now += timedelta(milliseconds=500)
    assert await processor(p, scan=scan).process_batch() == 1
    assert (await current(p)).retry_at == received + timedelta(milliseconds=1000)
    p.now = received + timedelta(milliseconds=999)
    assert await advance(p, batch=application(p)[0]) == 0
    p.now += timedelta(milliseconds=1)
    await advance(p, batch=application(p)[0])
    new = await current(p)
    assert new.operation_id != old and new.retry_at is None and new.response_evidence_id is None
    assert len(await requests(p)) == 2
    async with p.sessions.begin() as db:
        oldfact = await db.get(InboundEvidence, eid)
        assert (
            await application(p)[2].apply_result_in_session(
                db, line=await db.get(WorkLine, p.line.id), evidence=oldfact
            )
            == "IGNORED"
        )
    assert (await current(p)).operation_id == new.operation_id


@pytest.mark.parametrize("waiting", [False, True])
async def test_next_task_priority_is_atomic_with_concurrent_drain_admission(drainable, waiting):
    p = drainable
    batch, _, _ = application(p)
    if waiting:
        await advance(p, batch=batch)
        await decided(p, publish=True)
        await advance(p, batch=batch)
        p.now = (await current(p)).retry_at
    tid = await queued(p)
    await asyncio.gather(*(advance(p, batch=application(p)[0]) for _ in range(6)))
    assert len(await requests(p)) == int(waiting) and await current(p) is None
    async with p.sessions() as db:
        assert (await db.get(PickingTask, tid)).status == "PREPARING"
        prepares = list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.picking_task.prepare@v1")
                )
            ).all()
        )
        assert len(prepares) == 1 and prepares[0].picking_task_id == tid


@pytest.mark.parametrize("waiting", [False, True])
async def test_concurrent_scans_create_one_current_request(drainable, waiting):
    p = drainable
    if waiting:
        await advance(p, batch=application(p)[0])
        await decided(p, publish=True)
        await advance(p, batch=application(p)[0])
        p.now = (await current(p)).retry_at
    await asyncio.gather(*(advance(p, batch=application(p)[0]) for _ in range(6)))
    assert len(await requests(p)) == 1 + int(waiting)


@pytest.mark.parametrize("crash_at", ["request", "cursor", "prepare"])
async def test_crash_rolls_back_request_cursor_or_next_task_claim(drainable, crash_at):
    p = drainable
    batch, _, flow = application(p)
    tid = await queued(p) if crash_at == "prepare" else None
    target, name = (
        (flow.scheduler, "create_in_session")
        if crash_at == "request"
        else (flow.repository, "replace")
        if crash_at == "cursor"
        else (flow.prepare, "prepare_next_in_session")
    )
    original = getattr(target, name)

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline crash")

    setattr(target, name, crash)
    with pytest.raises(RuntimeError, match="offline crash"):
        await advance(p, batch=batch)
    assert not await requests(p) and await current(p) is None
    if tid:
        async with p.sessions() as db:
            assert (await db.get(PickingTask, tid)).status == "QUEUED"
    await advance(p, batch=application(p)[0])
    assert bool(await requests(p)) is (tid is None)


@pytest.mark.parametrize("mode", ["unknown", "unpublished", "reconciling"])
async def test_original_unknown_or_unpublished_request_is_never_replaced(drainable, mode):
    p = drainable
    batch, _, _ = application(p)
    await advance(p, batch=batch)
    old = (await current(p)).operation_id
    if mode == "unpublished":
        await decided(p)
    if mode == "reconciling":
        async with p.sessions.begin() as db:
            row = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == old))
            row.status = "RECONCILING"
    await queued(p)
    p.now += timedelta(days=1)
    assert await advance(p, batch=application(p)[0]) == 0
    assert len(await requests(p)) == 1 and (await current(p)).operation_id == old


async def test_wait_without_remaining_candidates_retires_current_and_old_result_cannot_reopen(drainable):
    p = drainable
    batch, _, _ = application(p)
    await advance(p, batch=batch)
    eid = await decided(p, publish=True)
    await advance(p, batch=batch)
    p.now = (await current(p)).retry_at
    p.positions = {p.return_id: "OFFLINE-LEFT-BUFFER"}
    await advance(p, batch=application(p)[0])
    assert await current(p) is None
    async with p.sessions.begin() as db:
        assert (
            await application(p)[2].apply_result_in_session(
                db, line=await db.get(WorkLine, p.line.id), evidence=await db.get(InboundEvidence, eid)
            )
            == "IGNORED"
        )


async def test_replaced_wait_history_can_be_deleted_before_fresh_recovery(drainable):
    p = drainable
    await advance(p, batch=application(p)[0])
    old = (await current(p)).operation_id
    eid = await decided(p, publish=True)
    await advance(p, batch=application(p)[0])
    p.now = (await current(p)).retry_at
    await advance(p, batch=application(p)[0])
    new = (await current(p)).operation_id
    async with p.sessions.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == old))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == eid))
    await decided(p, "READY", publish=True)
    await advance(p, batch=application(p)[0])
    assert (await current(p)).operation_id == new and (await current(p)).response_evidence_id is not None
    assert len(await requests(p)) == 1


async def test_current_ready_is_counted_by_business_blocker(drainable):
    p = drainable
    before = None
    async with p.sessions() as db:
        before = (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"]
    await advance(p, batch=application(p)[0])
    await decided(p, "READY", publish=True)
    await advance(p, batch=application(p)[0])
    async with p.sessions() as db:
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == before + 1


async def test_wait_with_unknown_head_position_keeps_original_wait_owner(drainable):
    p = drainable
    await advance(p, batch=application(p)[0])
    await decided(p, publish=True)
    await advance(p, batch=application(p)[0])
    old = await current(p)
    p.now, p.positions = old.retry_at, {}
    await advance(p, batch=application(p)[0])
    assert (await current(p)).operation_id == old.operation_id
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("state", ["PREPARING", "EXECUTING"])
async def test_active_task_prevents_new_drain(drainable, state):
    p = drainable
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = state
    assert await advance(p, batch=application(p)[0]) == 0
    assert not await requests(p)


async def test_prepare_not_ready_does_not_mean_no_eligible_task(drainable):
    p = drainable
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).run_mode = "MANUAL"
    assert await advance(p, batch=application(p)[0]) == 0
    assert not await requests(p)


async def test_ready_does_not_block_independent_prepare_or_grow_members(drainable):
    p = drainable
    await advance(p, batch=application(p)[0])
    await decided(p, "READY", publish=True)
    await advance(p, batch=application(p)[0])
    frozen = await current(p)
    tid = await queued(p)
    flow = application(p)[2]
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert (await flow.prepare.prepare_next_in_session(db, line, now=p.now)).prepared
    await advance(p, batch=application(p)[0])
    assert (await current(p)).return_ids == frozen.return_ids
    assert (await current(p)).operation_id == frozen.operation_id
    async with p.sessions() as db:
        assert (await db.get(PickingTask, tid)).status == "PREPARING"


async def test_result_application_and_publication_rollback_together(drainable):
    p = drainable
    await advance(p, batch=application(p)[0])
    eid = await decided(p, "READY")
    _, scan, flow = application(p)
    previous = flow._remember

    async def crash(*args, **kwargs):
        await previous(*args, **kwargs)
        raise RuntimeError("offline result crash")

    flow._remember = crash
    await processor(p, scan=scan).process_batch()
    assert (await current(p)).response_evidence_id is None
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, eid)
        assert fact.published_at is None
        fact.decision_next_attempt_at = p.now
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert (await current(p)).response_evidence_id == eid


@pytest.mark.parametrize("boundary", ["capacity", "location", "unqualified", "unpublished_fifo", "unknown_fifo"])
async def test_invalid_or_unknown_candidate_boundary_never_creates_drain(drainable, boundary):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = drainable
    if boundary == "capacity":
        p.buffer = None
    if boundary == "location":
        p.positions = {}
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        if boundary == "unqualified":
            row.ready_evidence_id = None
        if boundary == "unpublished_fifo":
            (await db.get(InboundEvidence, row.scan4_evidence_id)).published_at = None
        if boundary == "unknown_fifo":
            row.ready_evidence_id = None
            row.scan4_event_time = None
            row.scan4_evidence_id = None
    await advance(p, batch=application(p)[0])
    assert not await requests(p)


async def test_clean_migration_roundtrip_and_schema_match(database):
    from sqlalchemy import inspect

    from tests.support.postgresql_heavy import run_alembic

    url, sessions = database
    run_alembic("downgrade", "6680af0fe85b", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with sessions() as db:
        connection = await db.connection()
        columns = await connection.run_sync(
            lambda c: inspect(c).get_columns("automatic_picking_drains", schema="wes_biz")
        )
        assert {c["name"] for c in columns} == set(AutomaticPickingDrain.__table__.columns.keys())
        indexes = await connection.run_sync(
            lambda c: inspect(c).get_indexes("automatic_picking_drains", schema="wes_biz")
        )
        assert any(i["name"] == "ix_automatic_drain_current" and i["unique"] for i in indexes)


async def test_migration_refuses_to_discard_current_reservation(drainable):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    await advance(p, batch=application(p)[0])
    async with p.sessions() as db:
        url = str(db.bind.url.render_as_string(hide_password=False))
    with pytest.raises(subprocess.CalledProcessError):
        run_alembic("downgrade", "6680af0fe85b", database_url=url)
    assert await current(p) is not None


@pytest.mark.parametrize("kind", ["work", "feed"])
async def test_current_normal_service_keeps_drain_out_of_admission(drainable, kind):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.work_model import AutomaticPickingWork

    p = drainable
    flow = application(p)[2]
    async with p.sessions.begin() as db:
        if kind == "work":
            row = await db.get(AutomaticPickingWork, p.work.id)
            row.archived_at, row.phase = None, "PLAN_PENDING"
        else:
            row = await db.get(AutomaticPickingFeed, p.feed.id)
            row.archived_at, row.phase = None, "REQUESTED"
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert await flow.advance_in_session(db, line) == 0
    assert not await requests(p)


async def additional_return(p, *, bin_code="A000000002", timestamp=2000):
    from test_return_directions_postgresql import callback
    from test_return_fifo_postgresql import other_return, scan4

    original = p.return_id
    new = await other_return(p, bin_code=bin_code)
    await scan4(p, return_id=new, timestamp=timestamp)
    _, batch, scan = direction_application(p)
    assert await processor(p, scan=scan).process_batch() == 1
    await advance(p, batch=batch)
    p.return_id = new
    try:
        await callback(p)
    finally:
        p.return_id = original
    assert await processor(p, scan=scan).process_batch() == 1
    p.positions[new] = "OFFLINE-OUTLET"
    return new


@pytest.mark.parametrize("result", ["WAIT", "READY"])
async def test_new_candidates_only_enter_a_new_wait_decision_not_old_ready(drainable, result):
    p = drainable
    await advance(p, batch=application(p)[0])
    await decided(p, result, publish=True)
    await advance(p, batch=application(p)[0])
    old = await current(p)
    new_return = await additional_return(p)
    if result == "WAIT":
        p.now = old.retry_at
    await advance(p, batch=application(p)[0])
    live = await current(p)
    if result == "READY":
        assert live.operation_id == old.operation_id and live.return_ids == [p.return_id]
        assert len(await requests(p)) == 1
    else:
        assert live.operation_id != old.operation_id and live.return_ids == [p.return_id, new_return]
        assert (await requests(p))[-1].request_payload["data"]["required_slot_count"] == 2


async def test_unknown_head_cannot_be_overtaken_by_known_tail(drainable):
    p = drainable
    await additional_return(p)
    p.positions.pop(p.return_id)
    assert await advance(p, batch=application(p)[0]) == 0
    assert not await requests(p)


async def test_authoritative_buffer_capacity_is_validated_without_truncating_members(drainable):
    p = drainable
    await additional_return(p)
    p.buffer = MatchedDrainBuffer("OFFLINE-OUTLET", 1)
    with pytest.raises(ValueError, match="exceed authoritative buffer capacity"):
        await advance(p, batch=application(p)[0])
    assert not await requests(p)


@pytest.mark.parametrize("crash_at", ["request", "cursor"])
async def test_due_wait_replacement_rolls_back_to_exact_original_identity(drainable, crash_at):
    p = drainable
    await advance(p, batch=application(p)[0])
    await decided(p, publish=True)
    await advance(p, batch=application(p)[0])
    old = await current(p)
    p.now = old.retry_at
    batch, _, flow = application(p)
    target, name = (flow.scheduler, "create_in_session") if crash_at == "request" else (flow.repository, "replace")
    previous = getattr(target, name)

    async def crash(*args, **kwargs):
        await previous(*args, **kwargs)
        raise RuntimeError("offline WAIT replacement crash")

    setattr(target, name, crash)
    with pytest.raises(RuntimeError, match="offline WAIT replacement crash"):
        await advance(p, batch=batch)
    live = await current(p)
    assert (live.operation_id, live.response_evidence_id, live.retry_at, live.return_ids) == (
        old.operation_id,
        old.response_evidence_id,
        old.retry_at,
        old.return_ids,
    )
    assert len(await requests(p)) == 1
    await advance(p, batch=application(p)[0])
    assert (await current(p)).operation_id != old.operation_id
    assert len(await requests(p)) == 2


async def test_missing_business_flow_defers_result_until_original_owner_returns(drainable):
    p = drainable
    await advance(p, batch=application(p)[0])
    eid = await decided(p, "READY")
    await processor(p, scan=direction_application(p)[2]).process_batch()
    assert (await current(p)).response_evidence_id is None
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert (await current(p)).response_evidence_id == eid


async def test_closed_front_work_and_feed_are_not_drain_runtime_dependencies(drainable):
    from automatic_picking.application.feed_model import AutomaticPickingFeed, AutomaticPickingFeedScan
    from automatic_picking.application.work_model import AutomaticPickingWork

    p = drainable
    await advance(p, batch=application(p)[0])
    await decided(p, publish=True)
    await advance(p, batch=application(p)[0])
    old = await current(p)
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(AutomaticPickingFeedScan).where(AutomaticPickingFeedScan.feed_id == p.feed.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == p.feed.id))
    p.now = old.retry_at
    await advance(p, batch=application(p)[0])
    assert (await current(p)).operation_id != old.operation_id
    assert (await current(p)).return_ids == [p.return_id]


@pytest.mark.parametrize("state", ["PREPARING", "EXECUTING"])
async def test_due_wait_yields_to_an_already_active_task(drainable, state):
    p = drainable
    await advance(p, batch=application(p)[0])
    await decided(p, publish=True)
    await advance(p, batch=application(p)[0])
    p.now = (await current(p)).retry_at
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = state
    await advance(p, batch=application(p)[0])
    assert await current(p) is None
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("tail", ["unknown_position", "unqualified", "allocated"])
async def test_blocked_tail_cannot_bypass_capacity_of_confirmed_prefix(drainable, tail):
    from automatic_picking.application.return_allocation_flow import MatchedReturnFace
    from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from wes_plugin_sdk import BinReturnCandidate, wms_operations

    from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchScheduler
    from src.app.wms_integration.outbound_picking.services.return_batch_owner import ReturnBatchOwnerService

    p = drainable
    await additional_return(p)
    third = await additional_return(p, bin_code="A000000003", timestamp=3000)
    p.buffer = MatchedDrainBuffer("OFFLINE-OUTLET", 1)
    if tail == "unknown_position":
        p.positions.pop(third)
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturn, third)
        if tail == "unqualified":
            row.ready_evidence_id = None
        if tail == "allocated":
            line = await db.get(WorkLine, p.line.id, with_for_update=True)
            operation_id = new_uuid7()
            await ReturnAllocationRepository().add(
                db,
                line=line,
                face=MatchedReturnFace(p.face_id, line.id, "OFFLINE-RACK", "A"),
                operation_id=operation_id,
                rows=[row],
                completed=(),
                now=p.now,
            )
            await BinBatchScheduler(
                WmsConfirmationLifecycleService(workline_owner=ReturnBatchOwnerService())
            ).create_in_session(
                db,
                wms_operations.outbound_bin_return_batch(
                    operation_id=operation_id,
                    workline_code=line.line_code,
                    rack_id="OFFLINE-RACK",
                    rack_face="A",
                    return_candidates=(BinReturnCandidate(1, row.bin_code, "OFFLINE-OUTLET"),),
                ),
                workline_id=line.id,
                created_at=p.now,
            )
    with pytest.raises(ValueError, match="exceed authoritative buffer capacity"):
        await advance(p, batch=application(p)[0])
    assert not await requests(p) and await current(p) is None
