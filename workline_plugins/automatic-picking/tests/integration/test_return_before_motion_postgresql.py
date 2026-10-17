"""8E 只冻结运动前求值出口；位置、架面和运动依据均为显式离线输入。"""

import pytest
from sqlalchemy import select
from test_return_allocation_postgresql import (
    FaceReader,
    accept,
    add_qualified,
    advance,
    allocation,
    application,
    arrived,
    complete_transport,
    database,
    new_face,
    observed,
    pending,
    processor,
    qualified,
    result,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class MotionReader:
    def __init__(self, p):
        self.p = p

    async def read_in_session(self, db, *, workline_id):
        from automatic_picking.application.return_allocation_flow import MatchedReturnMotion

        faces = await FaceReader(self.p).read_in_session(db, workline_id=workline_id)
        return tuple(MatchedReturnMotion(face, face.arrival_evidence_id) for face in faces)


def motion_application(p):
    batch, scan = application(p)
    batch.return_batch_flow.motion_reader = MotionReader(p)
    return batch, scan


async def phase(p):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    async with p.sessions() as db:
        return await db.scalar(select(AutomaticPickingReturnMotion).order_by(AutomaticPickingReturnMotion.id.desc()))


async def test_no_candidate_phase_is_durable_and_new_candidate_does_not_reopen_it(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    from src.app.execution.models import WmsConfirmation

    p = qualified
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).archived_at = p.now
    batch, _ = motion_application(p)
    assert await advance(p, batch=batch) > 0
    current = await phase(p)
    assert current.closed_at is not None and current.allocation_id is None
    await add_qualified(p)
    assert await advance(p, batch=application(p)[0]) == 0
    async with p.sessions() as db:
        assert not list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
                )
            ).all()
        )


@pytest.mark.parametrize("unknown", ["order", "qualification", "position"])
async def test_unknown_head_is_not_no_candidate(qualified, unknown):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    from src.app.execution.models import InboundEvidence

    p = qualified
    if unknown == "position":
        p.positions.clear()
    else:
        async with p.sessions.begin() as db:
            row = await db.get(AutomaticPickingReturn, p.return_id)
            if unknown == "qualification":
                row.ready_evidence_id = None
            else:
                (await db.get(InboundEvidence, row.scan4_evidence_id)).published_at = None
    batch, _ = motion_application(p)
    assert await advance(p, batch=batch) > 0
    current = await phase(p)
    assert current.closed_at is None and current.allocation_id is None
    assert await allocation(p) is None
    assert await advance(p, batch=application(p)[0]) == 0


@pytest.mark.parametrize("decision", ["NO_BATCH", "READY"])
async def test_unpublished_result_never_finishes_phase(qualified, decision):
    p = qualified
    batch, scan = motion_application(p)
    assert await advance(p, batch=batch) > 0
    await result(p, result=decision)
    assert await advance(p, batch=batch) == 0
    assert (await phase(p)).closed_at is None
    assert await processor(p, scan=scan).process_batch() == 1
    assert await advance(p, batch=batch) > 0
    current = await phase(p)
    if decision == "NO_BATCH":
        assert current.closed_at is not None
        await add_qualified(p)
        assert await advance(p, batch=application(p)[0]) == 0
    else:
        assert current.closed_at is None
        assert (await allocation(p)).transport_task_id is not None


async def test_ready_phase_waits_for_physical_result_and_business_publication(qualified):
    from src.app.transport.models import TransportTask

    p = qualified
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    await result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    await accept(p)
    assert (await phase(p)).closed_at is None
    await add_qualified(p)
    await complete_transport(p)
    assert await advance(p, batch=batch) == 0
    assert (await phase(p)).closed_at is None
    await processor(p, scan=scan).process_batch()
    assert await advance(p, batch=application(p)[0]) > 0
    current = await phase(p)
    assert current.closed_at is not None
    assert await advance(p, batch=application(p)[0]) == 0
    async with p.sessions() as db:
        tasks = list((await db.scalars(select(TransportTask))).all())
        assert len(tasks) == 1 and tasks[0].kind == "BIN_MOVE"


async def test_original_unknown_request_is_recovered_before_empty_claimed_fifo(qualified):
    p = qualified
    await advance(p)
    original = await allocation(p)
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.closed_at is None and current.allocation_id is None
    assert (await allocation(p)).id == original.id
    await result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    await accept(p)
    assert (await phase(p)).closed_at is None
    await complete_transport(p)
    await processor(p, scan=scan).process_batch()
    assert await advance(p, batch=batch) > 0
    assert (await phase(p)).closed_at is not None
    assert (await allocation(p)).id == original.id  # 处理原义务后确实无当前可派发候选。


async def test_finished_original_batch_does_not_replace_final_motion_evaluation(qualified):
    p = qualified
    await advance(p)
    await result(p)
    await processor(p, scan=application(p)[1]).process_batch()
    await advance(p)
    await accept(p)
    tail, _ = await add_qualified(p)
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    assert (await phase(p)).allocation_id is None
    await complete_transport(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    current = await phase(p)
    final = await allocation(p)
    assert current.closed_at is None and current.allocation_id == final.id
    assert final.return_ids == [tail]


async def test_existing_no_batch_is_consumed_without_reopening_arrival(qualified):
    p = qualified
    await advance(p)
    await result(p, result="NO_BATCH")
    await processor(p, scan=application(p)[1]).process_batch()
    original = await allocation(p)
    await add_qualified(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.closed_at is not None and current.allocation_id == original.id
    assert (await allocation(p)).id == original.id


async def test_no_batch_does_not_skip_other_direct_obligations_of_current_rack(qualified):
    p = qualified
    p.faces = [await new_face(p, face="B")]
    await advance(p)
    other = await allocation(p)
    assert other.rack_face == "B" and other.response_evidence_id is None
    await add_qualified(p)
    p.faces = [await new_face(p)]
    await advance(p)
    await result(p, result="NO_BATCH")
    await processor(p, scan=application(p)[1]).process_batch()
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    assert (await phase(p)).closed_at is None


async def test_unknown_terminal_position_does_not_finish_motion_phase(qualified):
    p = qualified
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    await result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    await accept(p)
    await complete_transport(p, unknown=True)
    await processor(p, scan=scan).process_batch()
    assert await advance(p, batch=batch) == 0
    assert (await phase(p)).closed_at is None


async def test_published_rejection_ends_motion_wait_without_faking_return_handoff(qualified):
    from dataclasses import asdict
    from datetime import timedelta
    from types import SimpleNamespace as N

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.transport.contracts import TransportCaller, TransportOutcome
    from src.app.transport.models import TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    p = qualified
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    await result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    current = await allocation(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current.transport_task_id)
        )
        task.status, task.outcome_version = "REJECTED", 1
        task.outcome_json = asdict(
            TransportOutcome(
                task.transport_task_id,
                task.client_request_id,
                1,
                TransportCaller(str(p.line.id)),
                "REJECTED",
                "CAPACITY_REJECTED",
                (),
            )
        )
    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository()),),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await advance(p, batch=batch) == 0
    await processor(p, scan=scan).process_batch()
    assert await advance(p, batch=batch) > 0
    assert (await phase(p)).closed_at is not None
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.archived_at is None and row.allocation_id == current.id


async def test_closed_phase_only_retires_on_authoritative_new_arrival(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    p = qualified
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).archived_at = p.now
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    previous = await phase(p)
    await add_qualified(p)

    class UnknownFaces:
        async def read_in_session(self, db, *, workline_id):
            return None

    batch.return_batch_flow.face_reader = UnknownFaces()
    batch.return_batch_flow.motion_reader = None
    assert await advance(p, batch=batch) == 0
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnMotion, previous.id)).archived_at is None
    p.faces = [await new_face(p)]
    assert await advance(p, batch=application(p)[0]) == 1
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnMotion, previous.id)).archived_at is not None
    assert (await allocation(p)).arrival_evidence_id == p.faces[0][0]


@pytest.mark.parametrize("snapshot", ["empty", "other_rack"])
async def test_missing_rack_fact_cannot_retire_closed_phase_or_reopen_old_arrival(qualified, snapshot):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).archived_at = p.now
    await advance(p, batch=motion_application(p)[0])
    previous = await phase(p)
    original_faces = list(p.faces)
    p.faces = [] if snapshot == "empty" else [await new_face(p, rack="INDEPENDENT")]
    await advance(p, batch=application(p)[0])
    assert (await phase(p)).archived_at is None
    p.faces = original_faces
    await add_qualified(p)
    assert await advance(p, batch=application(p)[0]) == 0
    assert (await phase(p)).id == previous.id
    assert await allocation(p) is None


async def test_concurrent_scans_create_one_phase_and_one_frozen_request(qualified):
    import asyncio

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from src.app.execution.models import WmsConfirmation

    p = qualified
    batch, _ = motion_application(p)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=batch))
    async with p.sessions() as db:
        phases = list((await db.scalars(select(AutomaticPickingReturnMotion))).all())
        requests = list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
                )
            ).all()
        )
        assert len(phases) == len(requests) == 1
        assert phases[0].allocation_id == (await allocation(p)).id


@pytest.mark.parametrize("point", ["phase", "binding"])
async def test_phase_request_and_binding_roll_back_together(qualified, point):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from automatic_picking.application.return_motion_repository import ReturnMotionRepository

    from src.app.execution.models import WmsConfirmation

    class Crash(ReturnMotionRepository):
        async def add(self, *args, **kwargs):
            value = await super().add(*args, **kwargs)
            if point == "phase":
                raise RuntimeError("fixture motion crash")
            return value

        async def bind(self, *args, **kwargs):
            await super().bind(*args, **kwargs)
            raise RuntimeError("fixture motion crash")

    p = qualified
    batch, _ = motion_application(p)
    batch.return_batch_flow.motions = Crash()
    with pytest.raises(RuntimeError, match="fixture motion crash"):
        await advance(p, batch=batch)
    async with p.sessions() as db:
        assert not list((await db.scalars(select(AutomaticPickingReturnMotion))).all())
        assert not list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
                )
            ).all()
        )
    assert await allocation(p) is None
    assert await advance(p, batch=motion_application(p)[0]) > 0


async def test_close_crash_preserves_original_phase_and_result_for_retry(qualified):
    from automatic_picking.application.return_motion_repository import ReturnMotionRepository

    class Crash(ReturnMotionRepository):
        async def close(self, *args, **kwargs):
            await super().close(*args, **kwargs)
            raise RuntimeError("fixture close crash")

    p = qualified
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    original = await allocation(p)
    eid = await result(p, result="NO_BATCH")
    await processor(p, scan=scan).process_batch()
    batch.return_batch_flow.motions = Crash()
    with pytest.raises(RuntimeError, match="fixture close crash"):
        await advance(p, batch=batch)
    assert (await phase(p)).closed_at is None
    assert await advance(p, batch=application(p)[0]) > 0
    current = await phase(p)
    assert current.closed_at is not None and current.allocation_id == original.id
    assert (await allocation(p)).response_evidence_id == eid


async def test_ready_phase_recovers_after_handoff_history_cleanup_without_executing_task(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from automatic_picking.application.work_model import AutomaticPickingWork
    from sqlalchemy import delete

    from src.app.execution.models import WmsConfirmation
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = qualified
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    await result(p)
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    await accept(p)
    original = await allocation(p)
    await add_qualified(p)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        await db.execute(delete(AutomaticPickingReturn).where(AutomaticPickingReturn.archived_at.is_not(None)))
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.phase == "CLOSED"))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.archived_at.is_not(None)))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.status == "COMPLETED"))
    assert await advance(p, batch=application(p)[0]) == 0
    assert (await phase(p)).closed_at is None
    await complete_transport(p)
    await processor(p, scan=application(p)[1]).process_batch()
    assert await advance(p, batch=application(p)[0]) > 0
    current = await phase(p)
    assert current.closed_at is not None and current.allocation_id == original.id
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingReturnAllocation, original.id))
    assert await advance(p, batch=application(p)[0]) == 0
    assert (await phase(p)).id == current.id


async def test_old_result_replay_does_not_reopen_closed_phase_or_current_new_arrival(qualified):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from src.app.execution.models import InboundEvidence

    p = qualified
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    eid = await result(p, result="NO_BATCH")
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    old = await phase(p)
    p.faces = [await new_face(p)]
    await advance(p, batch=motion_application(p)[0])
    current = await phase(p)
    assert current.id != old.id and current.closed_at is None
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, eid)).published_at = None
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert await advance(p, batch=application(p)[0]) == 0
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnMotion, old.id)).archived_at is not None
        live = await db.get(AutomaticPickingReturnMotion, current.id)
        assert live.closed_at is None and live.allocation_id == current.allocation_id


async def test_pending_phase_does_not_block_independent_rack(qualified):
    p = qualified
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    original = await allocation(p)
    await add_qualified(p)
    p.faces.append(await new_face(p, rack="INDEPENDENT", face="B"))
    assert await advance(p, batch=batch) > 0
    following = await allocation(p)
    assert following.id != original.id and following.rack_id == "INDEPENDENT"
    assert (await phase(p)).allocation_id == following.id


async def test_migration_preserves_8d_and_refuses_loss_of_motion_exit(qualified, database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = qualified
    url, _ = database
    await advance(p)
    original = await allocation(p)
    run_alembic("downgrade", "a448148f9636", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert (await allocation(p)).id == original.id
    await advance(p, batch=motion_application(p)[0])
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "a448148f9636", database_url=url)
    assert "cannot discard automatic motion evaluation identities or exit facts" in error.value.stderr


async def test_motion_model_matches_migration(qualified):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare: (
                    kind != "table" or name == "automatic_picking_return_motions"
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingReturnMotion.metadata)

    async with qualified.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []


async def test_pending_motion_owns_workline_blocker_and_archiver(qualified):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from automatic_picking.application.work_repository import WorkRepository

    p = qualified
    works = WorkRepository()
    await advance(p)
    async with p.sessions() as db:
        before = await works.get_unfinished_workload_summary(db, p.line.id)
    await advance(p, batch=motion_application(p)[0])
    current = await phase(p)
    async with p.sessions.begin() as db:
        after = await works.get_unfinished_workload_summary(db, p.line.id)
        assert after["count"] == before["count"] + 1
        await works.archive_open_work(db, workline_id=p.line.id, archived_at=p.now)
        assert (await db.get(AutomaticPickingReturnMotion, current.id)).archived_at == p.now
        assert (await works.get_unfinished_workload_summary(db, p.line.id))["count"] == 0
