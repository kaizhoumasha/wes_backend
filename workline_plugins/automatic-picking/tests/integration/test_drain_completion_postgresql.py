"""9G：正常根收口及原离场义务交接，使用离线权威事实和隔离 PostgreSQL。"""

import pytest
from test_drain_departure_decision_postgresql import application, database, drainable, observed, pending, qualified
from test_drain_departure_postgresql import departures, phase
from test_drain_progress_postgresql import current
from test_drain_return_postgresql import movement_result
from test_return_allocation_postgresql import advance, arrived
from test_return_allocation_postgresql import result as batch_result
from test_returns_postgresql import processor
from test_source_rack_departure_decision_postgresql import decided

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def serviced(p):
    from src.utils.timezone import timezone

    await application(p)
    await advance(p, batch=p.app[0])
    await batch_result(p)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    await movement_result(p)
    p.now = timezone.now_for_db()
    await advance(p, batch=p.app[0])
    root = await current(p)
    assert root.returned_ids == root.return_ids
    assert (await phase(p)).departure_operation_id is not None
    return root


async def departing(p):
    root = await serviced(p)
    await decided(p, "READY")
    await advance(p, batch=p.app[0])
    return root, (await departures(p))[0]


async def accepted(p):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(1) == 1


async def test_all_original_returns_and_departure_handoff_close_only_local_drain(drainable):
    from automatic_picking.application.drain_model import AutomaticPickingDrain
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.transport.models import TransportTask

    p = drainable
    root, task = await departing(p)
    stage = await phase(p)
    await accepted(p)
    await advance(p, batch=p.app[0])
    assert await current(p) is None
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingDrain, root.id)).archived_at is not None
        assert (await db.get(TransportTask, task.id)).status == "ACCEPTED"
        assert (await db.get(AutomaticPickingReturnMotion, stage.id)).archived_at is None
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] > 0


async def test_each_rack_handoff_survives_cleaning_its_closed_motion(drainable):
    from automatic_picking.application.drain_model import AutomaticPickingDrain
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_departure_postgresql import departure_result

    from src.utils.timezone import timezone

    p = drainable
    await application(p, reservations=(("OFFLINE-DRAIN", ("A",)), ("SECOND", ("A",))))
    await advance(p, batch=p.app[0])
    await batch_result(p, selected=2)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    await movement_result(p)
    p.now = timezone.now_for_db()
    await advance(p, batch=p.app[0])
    root, first_stage = await current(p), await phase(p)
    assert root.returned_ids == root.return_ids and len(root.return_ids) == 2
    await decided(p, "READY", operation_id=first_stage.departure_operation_id)
    await advance(p, batch=p.app[0])
    first = (await departures(p))[0]
    assert first.request_json["rack_id"] == "OFFLINE-DRAIN"
    await departure_result(p, first)
    await advance(p, batch=p.app[0])
    assert (await current(p)).departure_handoffs == {"OFFLINE-DRAIN": first.transport_task_id}
    async with p.sessions.begin() as db:
        saved = await db.get(AutomaticPickingReturnMotion, first_stage.id)
        assert saved.archived_at is not None
        await db.delete(saved)
    await decided(p, "READY", operation_id=(await phase(p, "SECOND")).departure_operation_id)
    await advance(p, batch=p.app[0])
    second = (await departures(p))[-1]
    assert second.request_json["rack_id"] == "SECOND"
    await accepted(p)
    await advance(p, batch=p.app[0])
    assert await current(p) is None
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingDrain, root.id)).departure_handoffs == {
            "OFFLINE-DRAIN": first.transport_task_id,
            "SECOND": second.transport_task_id,
        }


async def test_concurrent_close_is_once_and_keeps_original_pending_action(drainable):
    import asyncio

    from automatic_picking.application.drain_model import AutomaticPickingDrain

    p = drainable
    root, task = await departing(p)
    await accepted(p)
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(6)))
    async with p.sessions() as db:
        saved = await db.get(AutomaticPickingDrain, root.id)
        assert saved.archived_at is not None
        assert saved.departure_handoffs == {"OFFLINE-DRAIN": task.transport_task_id}
    assert len(await departures(p)) == 1
    assert await current(p) is None


@pytest.mark.parametrize("crash_at", ["handoff", "closure"])
async def test_handoff_fact_and_root_exit_rollback_together(drainable, crash_at):
    p = drainable
    await departing(p)
    await accepted(p)
    flow = p.app[2]
    target, name = (
        (flow.inbound.departure, "_remember_handoff") if crash_at == "handoff" else (flow, "advance_in_session")
    )
    previous = getattr(target, name)

    async def crash(*args, **kwargs):
        await previous(*args, **kwargs)
        raise RuntimeError("offline drain closure crash")

    setattr(target, name, crash)
    with pytest.raises(RuntimeError, match="offline drain closure crash"):
        await advance(p, batch=p.app[0])
    saved = await current(p)
    assert saved.archived_at is None and saved.departure_handoffs == {}
    assert (await phase(p)).archived_at is None
    setattr(target, name, previous)
    await advance(p, batch=p.app[0])
    assert await current(p) is None


@pytest.mark.parametrize("publish_business", [False, True])
async def test_success_before_ack_requires_published_result_then_closes(drainable, publish_business):
    from automatic_picking.application.drain_model import AutomaticPickingDrain
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_departure_postgresql import departure_result

    p = drainable
    root, task = await departing(p)
    stage = await phase(p)
    await departure_result(p, task, process=publish_business)
    await advance(p, batch=p.app[0])
    if not publish_business:
        assert (await current(p)).departure_handoffs == {}
        await processor(p, scan=p.app[1]).process_batch()
        await advance(p, batch=p.app[0])
    assert await current(p) is None
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingDrain, root.id)).departure_handoffs == {
            "OFFLINE-DRAIN": task.transport_task_id
        }
        assert (await db.get(AutomaticPickingReturnMotion, stage.id)).archived_at is not None


@pytest.mark.parametrize("waiting", ["request", "WAIT", "unpublished_READY", "PENDING", "DELIVERY_UNKNOWN"])
async def test_unconfirmed_departure_never_becomes_normal_closure(drainable, waiting):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    p = drainable
    root = await serviced(p)
    if waiting != "request":
        await decided(p, "WAIT" if waiting == "WAIT" else "READY", publish=waiting != "unpublished_READY")
        await advance(p, batch=p.app[0])
    if waiting == "DELIVERY_UNKNOWN":

        class Provider:
            async def submit(self, **kwargs):
                return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, kwargs["transport_task_id"])

        p.inbound_service.provider = Provider()
        assert await p.inbound_service.submit_pending_tasks(1) == 1
    await advance(p, batch=p.app[0])
    saved = await current(p)
    assert saved.id == root.id and saved.departure_handoffs == {} and saved.archived_at is None


async def test_departure_handoff_without_original_return_success_does_not_close(drainable):
    from test_drain_departure_recovery_postgresql import departing as without_returns

    p = drainable
    await without_returns(p)
    await accepted(p)
    await advance(p, batch=p.app[0])
    saved = await current(p)
    assert saved.returned_ids == [] and saved.return_ids
    assert set(saved.departure_handoffs) == {"OFFLINE-DRAIN"}
    assert saved.archived_at is None


async def clean_closed_root(p, root):
    from automatic_picking.application.drain_model import AutomaticPickingDrain, AutomaticPickingDrainRack
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from sqlalchemy import delete, select

    from src.app.execution.models import WmsConfirmation

    async with p.sessions.begin() as db:
        allocations = (
            await db.scalars(
                select(AutomaticPickingReturnAllocation).where(AutomaticPickingReturnAllocation.drain_id == root.id)
            )
        ).all()
        assert all(a.settled_at is not None for a in allocations)
        for allocation in allocations:
            await db.delete(allocation)
        await db.execute(delete(AutomaticPickingDrainRack).where(AutomaticPickingDrainRack.drain_id == root.id))
        await db.delete(await db.get(AutomaticPickingDrain, root.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == root.operation_id))


@pytest.mark.parametrize("cleanup", [False, True])
async def test_original_departure_recovers_after_root_exit_without_executing(drainable, cleanup):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_departure_decision_postgresql import install
    from test_drain_departure_postgresql import departure_result

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = drainable
    root, task = await departing(p)
    stage = await phase(p)
    await accepted(p)
    await advance(p, batch=p.app[0])
    assert await current(p) is None
    if cleanup:
        await clean_closed_root(p, root)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    install(p)
    await departure_result(p, task)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        saved = await db.get(AutomaticPickingReturnMotion, stage.id)
        assert saved.archived_at is not None
        assert saved.departure_transport_task_id == task.transport_task_id
    assert await current(p) is None and len(await departures(p)) == 1


async def test_late_unknown_failure_keeps_original_action_and_does_not_reopen_root(drainable):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_departure_decision_postgresql import install, requests
    from test_drain_departure_postgresql import departure_result

    from src.app.transport.models import TransportTask

    p = drainable
    root, task = await departing(p)
    stage = await phase(p)
    await accepted(p)
    await advance(p, batch=p.app[0])
    await clean_closed_root(p, root)
    install(p)
    await departure_result(p, task, unknown=True)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        assert (await db.get(TransportTask, task.id)).status == "RECONCILING"
        saved = await db.get(AutomaticPickingReturnMotion, stage.id)
        assert saved.archived_at is None and saved.departure_transport_task_id == task.transport_task_id
    assert await current(p) is None
    assert len(await requests(p)) == 1 and len(await departures(p)) == 1


@pytest.mark.parametrize("acked", [False, True], ids=["before_ack", "after_ack"])
@pytest.mark.parametrize("known_position", [False, True])
async def test_failed_result_before_first_handoff_scan_preserves_ack_fact(drainable, acked, known_position):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_drain_departure_decision_postgresql import install, requests
    from test_drain_inbound_postgresql import publisher

    from src.app.transport.models import TransportTask
    from src.core.uuid7 import new_uuid7

    p = drainable
    root, task = await departing(p)
    stage = await phase(p)
    if acked:
        await accepted(p)
    identity = new_uuid7()
    data = {
        "transport_task_id": task.transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "OFFLINE-DRAIN",
        "status": "FAILED",
        "position_unknown": not known_position,
        "failure_code": "OFFLINE_FAILURE",
    }
    if known_position:
        data.update(final_position={"kind": "RACK_POSITION", "location_code": "OFFLINE-DRAIN-WORK"}, arrival_face="A")
    message = {"operation": "transport.task.resulted@v1", "operation_id": identity, "timestamp": 1, "data": data}
    service = p.inbound_service
    assert (
        await service.record_callback(
            operation_id=identity,
            operation=message["operation"],
            message=message,
            payload=data,
            rejection_reason_code=None,
        )
    )["code"] == "RECEIVED"
    assert await service.process_pending_evidence(100) == 1
    assert await service.publish_pending_outcomes(100, await publisher(p)) == 1
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    if acked:
        assert await current(p) is None
        await clean_closed_root(p, root)
    else:
        saved = await current(p)
        assert saved.id == root.id and saved.departure_handoffs == {}
    install(p)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        saved_task = await db.get(TransportTask, task.id)
        assert saved_task.status == ("FAILED" if known_position else "RECONCILING")
        assert (saved_task.result_deadline_at is not None) is acked
        saved = await db.get(AutomaticPickingReturnMotion, stage.id)
        assert saved.archived_at is None and saved.departure_transport_task_id == task.transport_task_id
        assert saved.departure_operation_id == stage.departure_operation_id
    assert len(await requests(p)) == 1 and len(await departures(p)) == 1


async def test_old_result_and_root_replay_do_not_apply_to_new_drain(drainable):
    from dataclasses import replace

    from test_drain_departure_postgresql import departure_result
    from test_drain_inbound_postgresql import racks
    from test_drain_progress_postgresql import additional_return
    from test_drain_progress_postgresql import decided as drain_decided

    from src.app.execution.models import InboundEvidence

    p = drainable
    old, task = await departing(p)
    await accepted(p)
    await advance(p, batch=p.app[0])
    new_return = await additional_return(p)
    await advance(p, batch=p.app[0])
    new = await current(p)
    assert new.id != old.id and new.return_ids == [new_return]
    eid = await drain_decided(p, "READY", publish=True)
    p.inbound_inputs = {
        rack: replace(value, operation_id=new.operation_id, ready_evidence_id=eid)
        for rack, value in p.inbound_inputs.items()
    }
    await advance(p, batch=p.app[0])
    async with p.sessions.begin() as db:
        assert (
            await p.app[2].apply_result_in_session(
                db, line=p.line, evidence=await db.get(InboundEvidence, old.response_evidence_id)
            )
            == "IGNORED"
        )
    await departure_result(p, task)
    await advance(p, batch=p.app[0])
    saved = await current(p)
    assert saved.id == new.id and saved.returned_ids == [] and saved.departure_handoffs == {}
    new_racks = [r for r in await racks(p) if r.drain_id == new.id]
    assert len(new_racks) == 1 and new_racks[0].arrival_evidence_id is None


@pytest.mark.parametrize("bad_handoffs", [{"NOT_RESERVED": "original"}, {"OFFLINE-DRAIN": " "}])
async def test_invalid_persisted_handoff_scope_fails_closed(drainable, bad_handoffs):
    from automatic_picking.application.drain_model import AutomaticPickingDrain

    p = drainable
    root = await serviced(p)
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingDrain, root.id)).departure_handoffs = bad_handoffs
    with pytest.raises(ValueError, match="handoffs differ from original reservation"):
        await advance(p, batch=p.app[0])
    assert (await current(p)).archived_at is None


async def test_9f_upgrade_preserves_members_and_does_not_invent_handoffs(drainable, database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.drain_model import AutomaticPickingDrain

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    before = await serviced(p)
    url, _ = database
    run_alembic("downgrade", "588d54e9fe3e", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    saved = await current(p)
    assert (saved.id, saved.operation_id, saved.return_ids, saved.returned_ids) == (
        before.id,
        before.operation_id,
        before.return_ids,
        before.returned_ids,
    )
    assert saved.departure_handoffs == {} and saved.archived_at is None

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare: (
                    kind != "table" or name == "automatic_picking_drains"
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingDrain.metadata)

    async with p.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []


async def test_downgrade_rejects_discarding_handoff_facts(drainable, database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    await departing(p)
    await accepted(p)
    await advance(p, batch=p.app[0])
    url, _ = database
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "588d54e9fe3e", database_url=url)
    assert "cannot discard automatic drain departure handoffs" in error.value.stderr
