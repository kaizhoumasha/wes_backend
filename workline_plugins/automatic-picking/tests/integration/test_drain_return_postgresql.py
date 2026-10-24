"""9C：原成员 return_batch 与 BIN_MOVE 成功事实交接，真实事务，无外部派发。"""

import pytest
from sqlalchemy import select
from test_drain_inbound_postgresql import database, drainable, observed, pending, qualified, started
from test_drain_inbound_postgresql import result as rack_result
from test_drain_progress_postgresql import current
from test_return_allocation_postgresql import advance, allocation, application, arrived
from test_return_allocation_postgresql import result as batch_result
from test_returns_postgresql import processor

from src.app.execution.models import WmsConfirmation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def returned_to_face(p):
    task = await started(p)
    await rack_result(p, task)
    await advance(p, batch=p.app[0])
    p.faces = []
    batch, _ = application(p)
    p.app[0].return_batch_flow = batch.return_batch_flow
    p.app[1].return_batch_flow = batch.return_batch_flow
    return p.app[0], p.app[1]


async def requests(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(WmsConfirmation)
                    .where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
                    .order_by(WmsConfirmation.id)
                )
            ).all()
        )


async def test_arrived_drain_rack_requests_only_original_fifo_members(drainable):
    p = drainable
    batch, _ = await returned_to_face(p)
    await advance(p, batch=batch)
    rows = await requests(p)
    assert len(rows) == 1
    root = await current(p)
    a = await allocation(p)
    assert a.drain_id == root.id
    assert a.return_ids == root.return_ids == [p.return_id]
    assert rows[0].request_payload["data"]["rack_id"] == "OFFLINE-DRAIN"
    assert rows[0].picking_task_id is None
    await advance(p, batch=batch)
    assert len(await requests(p)) == 1


async def created(p, *, selected=1):
    batch, scan = await returned_to_face(p)
    await advance(p, batch=batch)
    await batch_result(p, selected=selected)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    return await allocation(p)


async def movement_result(p, a=None, *, unknown=False, partial=False, publish=True, process=True):
    from test_drain_inbound_postgresql import publisher

    from src.app.transport.models import TransportMember
    from src.core.uuid7 import new_uuid7

    a = a or await allocation(p)
    async with p.sessions() as db:
        members = list(
            (
                await db.scalars(
                    select(TransportMember)
                    .where(TransportMember.transport_task_id == a.transport_task_id)
                    .order_by(TransportMember.ordinal)
                )
            ).all()
        )
    results = []
    for index, member in enumerate(members):
        missing = unknown or (partial and index > 0)
        item = {
            "container_id": member.object_id,
            "status": "FAILED" if missing else "SUCCEEDED",
            "position_unknown": missing,
        }
        if missing:
            item["failure_code"] = "POSITION_UNKNOWN"
        else:
            item["final_position"] = member.target_json
        results.append(item)
    from src.app.wms_adapter.transport_wire import POSITION_OPERATION
    from tests.support.transport_callbacks import record_valid_callback

    for item in results:
        if item["status"] == "SUCCEEDED":
            await record_valid_callback(
                p.inbound_service,
                operation_id=new_uuid7(),
                transport_task_id=a.transport_task_id,
                operation=POSITION_OPERATION,
                timestamp=1,
                payload={
                    "container_id": item["container_id"],
                    "milestone": "TARGET_PLACED",
                    "final_position": item["final_position"],
                },
            )
            assert await p.inbound_service.process_pending_evidence(100) == 1
    payload = {
        "transport_task_id": a.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": sorted(results, key=lambda r: r["container_id"]),
    }
    identity = new_uuid7()
    message = {"operation": "transport.task.resulted@v1", "operation_id": identity, "timestamp": 1, "data": payload}
    assert (
        await p.inbound_service.record_callback(
            operation_id=identity,
            operation=message["operation"],
            message=message,
            payload=payload,
            rejection_reason_code=None,
        )
    )["code"] == "RECEIVED"
    assert await p.inbound_service.process_pending_evidence(100) == 1
    if publish:
        assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
        if process:
            await processor(p, scan=p.app[1]).process_batch()


async def test_positive_success_transfers_to_original_root_and_replays_once(drainable):
    p = drainable
    a = await created(p)
    assert (await current(p)).returned_ids == []
    await movement_result(p, a)
    await advance(p, batch=p.app[0])
    assert (await current(p)).returned_ids == [p.return_id]
    assert (await allocation(p)).settled_at is not None
    assert (await current(p)).archived_at is None  # 9C 不关闭 drain。
    await advance(p, batch=p.app[0])
    assert (await current(p)).returned_ids == [p.return_id]
    assert len(await requests(p)) == 1


async def test_partial_success_records_only_authoritative_original_members(drainable):
    from test_drain_progress_postgresql import additional_return

    p = drainable
    second = await additional_return(p)
    a = await created(p, selected=2)
    assert a.return_ids == [p.return_id, second]
    await movement_result(p, a, partial=True)
    await advance(p, batch=p.app[0])
    assert (await current(p)).returned_ids == [p.return_id]
    assert (await allocation(p)).settled_at is None


async def test_new_fifo_member_is_not_added_to_old_reservation(drainable):
    from test_drain_progress_postgresql import additional_return

    p = drainable
    batch, _ = await returned_to_face(p)
    late = await additional_return(p)
    await advance(p, batch=batch)
    assert (await allocation(p)).return_ids == [p.return_id]
    assert late not in (await current(p)).return_ids
    assert [c["bin_code"] for c in (await requests(p))[0].request_payload["data"]["return_candidates"]] == [
        "A123456789"
    ]


async def test_two_entry_concurrency_claims_original_once_and_normal_success_counts(drainable):
    import asyncio

    p = drainable
    batch, scan = await returned_to_face(p)
    p.faces = [(p.face_id, "OFFLINE-RACK", "A")]
    other, _ = application(p)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=other))
    assert len(await requests(p)) == 1
    a = await allocation(p)
    assert a.drain_id == (await current(p)).id
    assert a.rack_id == "OFFLINE-RACK"
    await batch_result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    await movement_result(p)
    await asyncio.gather(*(advance(p, batch=owner) for owner in (batch, other, batch, other)))
    assert (await current(p)).returned_ids == [p.return_id]
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("point", ["claim", "request", "transport", "success"])
async def test_transaction_crash_recovers_original_request_action_and_success(drainable, point):
    from src.app.workline.models import WorkLine

    p = drainable
    batch, scan = await returned_to_face(p)
    if point in {"transport", "success"}:
        await advance(p, batch=batch)
        await batch_result(p)
        await processor(p, scan=scan).process_batch()
    if point == "success":
        await advance(p, batch=batch)
        await movement_result(p)
    flow = batch.return_batch_flow
    original = None
    if point in {"claim", "transport"}:
        target, name = (flow.repository, "add") if point == "claim" else (flow.creator, "create")
        original = getattr(target, name)

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("offline crash")

        setattr(target, name, crash)
        with pytest.raises(RuntimeError, match="offline crash"):
            await advance(p, batch=batch)
        setattr(target, name, original)
    else:
        with pytest.raises(RuntimeError, match="offline crash"):
            async with p.sessions.begin() as db:
                line = await db.get(WorkLine, p.line.id, with_for_update=True)
                await batch.advance_completed_in_session(db, line)
                raise RuntimeError("offline crash")
    if point in {"claim", "request"}:
        assert not await requests(p)
    if point == "success":
        assert (await current(p)).returned_ids == []
        assert (await allocation(p)).settled_at is None
    await advance(p, batch=batch)
    assert len(await requests(p)) == 1
    if point == "transport":
        assert (await allocation(p)).transport_task_id is not None
    if point == "success":
        assert (await current(p)).returned_ids == [p.return_id]


@pytest.mark.parametrize("state", ["accepted", "unknown", "host_unpublished", "business_unpublished"])
async def test_no_success_fact_from_acceptance_unknown_or_unpublished_result(drainable, state):
    from src.app.transport.models import TransportTask

    p = drainable
    a = await created(p)
    if state == "accepted":
        async with p.sessions.begin() as db:
            task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
            task.status = "ACCEPTED"
    else:
        await movement_result(
            p,
            a,
            unknown=state == "unknown",
            publish=state != "host_unpublished",
            process=state != "business_unpublished",
        )
    await advance(p, batch=p.app[0])
    assert (await current(p)).returned_ids == []
    assert (await allocation(p)).settled_at is None
    assert (await allocation(p)).transport_task_id == a.transport_task_id


@pytest.mark.parametrize("fault", ["unknown_position", "newer_cause", "business_unpublished", "unhanded"])
async def test_old_or_unhanded_ingress_cannot_authorize_drain_return(drainable, fault):
    from automatic_picking.application.drain_model import AutomaticPickingDrainRack

    from src.app.execution.models import InboundEvidence, PositionProjection

    p = drainable
    batch, _ = await returned_to_face(p)
    async with p.sessions.begin() as db:
        rack = await db.scalar(select(AutomaticPickingDrainRack))
        # 另一入口声称同一到位，不能绕过9B当前事实与持久交接。
        p.faces = [(rack.arrival_evidence_id, "OFFLINE-DRAIN", "A")]
        if fault == "unhanded":
            rack.arrival_evidence_id = None
        elif fault == "business_unpublished":
            (await db.get(InboundEvidence, rack.arrival_evidence_id)).published_at = None
        else:
            projection = await db.scalar(
                select(PositionProjection).where(PositionProjection.object_id == "OFFLINE-DRAIN")
            )
            if fault == "unknown_position":
                projection.position_unknown = True
            else:
                projection.source_causal_token += 100
    # 只运行回架入口，unhanded 不由测试顺便执行的9B扫描自动补齐。
    async with p.sessions.begin() as db:
        from src.app.workline.models import WorkLine

        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        await batch.return_batch_flow.advance_in_session(db, line)
    assert not await requests(p)


async def test_rejected_settlement_is_not_success_and_keeps_member_for_later(drainable):
    from test_drain_inbound_postgresql import publisher

    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    p = drainable
    a = await created(p)

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode.REJECTED, kwargs["transport_task_id"], "RCS_TASK_REJECTED")

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(100) == 1
    assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert (await allocation(p)).settled_at is not None
    assert (await allocation(p)).transport_task_id == a.transport_task_id
    assert (await current(p)).returned_ids == []
    assert len(await requests(p)) == 1


async def test_no_batch_closes_only_this_face_and_never_marks_original_returned(drainable):
    from test_drain_progress_postgresql import additional_return

    p = drainable
    batch, scan = await returned_to_face(p)
    await advance(p, batch=batch)
    await batch_result(p, result="NO_BATCH")
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    await additional_return(p)
    await advance(p, batch=batch)
    assert len(await requests(p)) == 1
    assert (await current(p)).returned_ids == []
    assert (await current(p)).archived_at is None


async def test_archived_return_can_be_removed_before_success_then_allocation_history_after_handoff(drainable):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from sqlalchemy import delete

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding
    from src.app.transport.models import TransportMember, TransportTask

    p = drainable
    a = await created(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
        task.status = "ACCEPTED"
        client_id = task.client_request_id
    await advance(p, batch=p.app[0])
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.archived_at is not None
        await db.delete(row)
    assert (await current(p)).returned_ids == []
    await movement_result(p, a)
    await advance(p, batch=p.app[0])
    done = await allocation(p)
    assert done.settled_at is not None and done.handed_off_at is not None
    assert (await current(p)).returned_ids == [p.return_id]
    async with p.sessions.begin() as db:
        # 当前事实已交接；本例没有 Motion 或其它当前引用保留该闭合 Allocation。
        await db.execute(delete(AutomaticPickingReturnAllocation).where(AutomaticPickingReturnAllocation.id == a.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == a.operation_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == a.transport_task_id))
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == client_id)
        )
        await db.execute(delete(InboundEvidence).where(InboundEvidence.transport_task_id == a.transport_task_id))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
    # Fresh flow needs neither closed Return nor its Allocation/Transport/Confirmation history.
    other, _ = application(p)
    p.positions = {}
    await advance(p, batch=other)
    assert (await current(p)).returned_ids == [p.return_id]
    assert (await current(p)).archived_at is None
    assert not await requests(p)


async def test_same_barcode_new_return_id_cannot_enter_original_reservation(drainable):
    from test_drain_progress_postgresql import additional_return

    p = drainable
    a = await created(p)
    await movement_result(p, a)
    await advance(p, batch=p.app[0])
    original = p.return_id
    later = await additional_return(p, bin_code="A123456789", timestamp=3000)
    await advance(p, batch=p.app[0])
    assert later != original
    assert (await current(p)).return_ids == [original]
    assert (await current(p)).returned_ids == [original]
    assert len(await requests(p)) == 1


async def two_racks(p):
    from dataclasses import replace

    from test_drain_inbound_postgresql import inbound_application
    from test_drain_inbound_postgresql import tasks as rack_tasks
    from test_drain_progress_postgresql import additional_return, decided
    from test_drain_progress_postgresql import application as drain_application

    from src.app.execution.models import InboundEvidence
    from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
    from src.utils.canonical_json import canonical_json_digest

    second = await additional_return(p)
    batch, scan, _ = drain_application(p)
    await advance(p, batch=batch)
    eid = await decided(p, "READY")
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, eid)
        fact.normalized_payload = {
            **fact.normalized_payload,
            "data": {
                "result": "READY",
                "racks": [
                    {"rack_id": "OFFLINE-DRAIN", "rack_face": ["A"]},
                    {"rack_id": "OFFLINE-DRAIN-2", "rack_face": ["A"]},
                ],
            },
        }
        fact.payload_digest = canonical_json_digest(fact.normalized_payload)
        db.add(
            WorkLinePosition(
                workline_id=p.line.id,
                workline_code=p.line.line_code,
                position_code="DRAIN",
                position_name="offline",
                position_type="RACK_POSITION",
                position_role="SMT_RETURN_RACK_POSITION",
                allowed_rack_kind="RETURN",
                logic_location_code="OFFLINE-DRAIN-WORK",
                capacity=4,
                enabled=True,
            )
        )
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    p.app = await inbound_application(p)
    p.inbound_inputs["OFFLINE-DRAIN-2"] = replace(p.inbound_inputs["OFFLINE-DRAIN"], rack_id="OFFLINE-DRAIN-2")
    await advance(p, batch=p.app[0])
    for task in await rack_tasks(p):
        await rack_result(p, task)
    await advance(p, batch=p.app[0])
    p.faces = []
    rb, _ = application(p)
    p.app[0].return_batch_flow = rb.return_batch_flow
    p.app[1].return_batch_flow = rb.return_batch_flow
    return second


async def test_second_rack_can_progress_after_first_acceptance_without_waiting_unknown_result(drainable):
    from src.app.transport.models import TransportTask

    p = drainable
    second = await two_racks(p)
    batch, scan = p.app[:2]
    await advance(p, batch=batch)
    first = await allocation(p)
    await batch_result(p, selected=1)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    first = await allocation(p)
    await advance(p, batch=batch)
    assert len(await requests(p)) == 1  # 尚未接纳，FIFO 队头不越过。
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == first.transport_task_id))
        task.status = "ACCEPTED"
    await advance(p, batch=batch)
    assert len(await requests(p)) == 2
    other = await allocation(p)
    assert other.rack_id != first.rack_id and other.return_ids == [second]
    await movement_result(p, first, unknown=True)
    await advance(p, batch=batch)
    await batch_result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    other = await allocation(p)
    await movement_result(p, other)
    await advance(p, batch=batch)
    assert (await current(p)).returned_ids == [second]
    assert (await current(p)).archived_at is None
    async with p.sessions() as db:
        old = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == first.transport_task_id))
        assert old.status == "RECONCILING"  # 独立架成功不改写原未知动作。


@pytest.mark.parametrize("state", ["pending", "unpublished", "WAIT"])
async def test_ordinary_entry_respects_only_original_pending_decision_dependency(drainable, state):
    from test_drain_progress_postgresql import application as drain_application
    from test_drain_progress_postgresql import decided

    p = drainable
    batch, _scan, _ = drain_application(p)
    await advance(p, batch=batch)
    if state != "pending":
        await decided(p, state if state == "WAIT" else "READY", publish=state == "WAIT")
        await advance(p, batch=batch)
    ordinary, _ = application(p)  # 显式已给定普通架面，与排空进场无关。
    await advance(p, batch=ordinary)
    assert len(await requests(p)) == (1 if state == "WAIT" else 0)
    assert (await current(p)).returned_ids == []


async def test_success_recovers_on_inactive_workline_without_executing_or_new_input(drainable):
    from src.app.workline.models import WorkLine

    p = drainable
    a = await created(p)
    await movement_result(p, a)
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).is_active = False
    p.positions = {}
    other, _ = application(p)
    await advance(p, batch=other)
    assert (await current(p)).returned_ids == [p.return_id]
    assert len(await requests(p)) == 1


@pytest.mark.parametrize(
    "field,value", [("status", "FAILED"), ("source_evidence_id", 999), ("caller", {"workline_id": "999"})]
)
async def test_changed_published_result_cannot_create_success_fact(drainable, field, value):
    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    p = drainable
    a = await created(p)
    await movement_result(p, a)
    async with p.sessions.begin() as db:
        fact = await db.scalar(select(InboundEvidence).where(InboundEvidence.transport_task_id == a.transport_task_id))
        fact.normalized_payload = {**fact.normalized_payload, field: value}
        fact.payload_digest = canonical_json_digest(fact.normalized_payload)
    with pytest.raises(ValueError, match="exact published allocation"):
        await advance(p, batch=p.app[0])
    assert (await current(p)).returned_ids == []
    assert (await allocation(p)).settled_at is None


@pytest.mark.parametrize("guard", ["ownership", "success_only"])
async def test_existing_9b_upgrade_schema_and_lossy_downgrade_guard(drainable, database, guard):
    import subprocess

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.drain_model import AutomaticPickingDrain
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    batch, scan = await returned_to_face(p)
    before = await current(p)
    url, _ = database
    run_alembic("downgrade", "40e50581119c", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    root = await current(p)
    assert (root.id, root.operation_id, root.return_ids, root.response_evidence_id) == (
        before.id,
        before.operation_id,
        before.return_ids,
        before.response_evidence_id,
    )
    assert root.returned_ids == []

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare: (
                    kind != "table" or name in {"automatic_picking_drains", "automatic_picking_return_allocations"}
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingDrain.metadata)

    async with p.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []
    await advance(p, batch=batch)
    if guard == "success_only":
        await batch_result(p)
        await processor(p, scan=scan).process_batch()
        await advance(p, batch=batch)
        await movement_result(p)
        await advance(p, batch=batch)
        a = await allocation(p)
        async with p.sessions.begin() as db:
            await db.delete(await db.get(AutomaticPickingReturnAllocation, a.id))
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "40e50581119c", database_url=url)
    assert "cannot discard automatic drain return ownership or successful member facts" in error.value.stderr
