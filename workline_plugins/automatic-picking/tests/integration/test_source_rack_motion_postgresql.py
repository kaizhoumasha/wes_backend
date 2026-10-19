"""普通来源架 CTU02 的离线事务与恢复验收。"""

import pytest
from test_return_allocation_postgresql import advance, arrived, database, observed, pending, qualified
from test_return_before_motion_postgresql import motion_application, phase
from test_return_motion_binding_postgresql import bound, empty_candidates

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_completed_batch_recovers_source_motion_without_executing_task(bound):
    p = bound
    await empty_candidates(p)
    batch, _ = motion_application(p)
    calls = []

    class Owner:
        async def advance_in_session(self, db, line):
            calls.append(line.id)
            return 0

    batch.rack_motion_flow = Owner()
    await advance(p, batch=batch)
    assert calls == [p.line.id]


async def rotation_application(p):
    from datetime import timedelta
    from types import SimpleNamespace as N

    from automatic_picking.application.source_rack_motion import MatchedSourceRackRotation, SourceRackMotionFlow
    from sqlalchemy import select
    from wes_plugin_sdk import TransportRackPosition

    from src.app.execution.models import PositionProjection
    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    await empty_candidates(p)
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        projection = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
            )
        )
        projection.source_effect_phase = "FINAL_RESULT"
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=2,
            rack_id="OFFLINE-RACK",
            rack_face="B",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        p.next_member_id = member.id
    p.rotations = (
        MatchedSourceRackRotation(
            current.id, p.next_member_id, "OFFLINE-RACK", TransportRackPosition("OFFLINE-FIVE"), "B"
        ),
    )

    class Reader:
        async def read_in_session(self, db, *, workline_id):
            return p.rotations

    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.bin_batch import (
        BinBatchScheduler,
        BinInboundBatchOwnerService,
    )

    scheduler = BinBatchScheduler(WmsConfirmationLifecycleService(workline_owner=BinInboundBatchOwnerService()))
    flow = SourceRackMotionFlow(
        batch.return_batch_flow, ReliableRackTransportCreator(service), scheduler, Reader(), clock=lambda: p.now
    )
    batch.rack_motion_flow = scan.rack_motion_flow = flow
    return batch, scan, flow


async def test_creates_and_binds_one_rotation_atomically(bound):
    from sqlalchemy import select

    from src.app.transport.models import TransportTask

    p = bound
    batch, _, _ = await rotation_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert getattr(current, "rotation_transport_task_id", None) is not None
    await advance(p, batch=batch)
    async with p.sessions() as db:
        rotations = list((await db.scalars(select(TransportTask).where(TransportTask.kind == "RACK_ROTATE"))).all())
        assert len(rotations) == 1
        assert rotations[0].transport_task_id == current.rotation_transport_task_id


async def rotation_result(p, batch, scan, *, face="B", unknown=False, publish=True):
    from datetime import timedelta
    from types import SimpleNamespace as N

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository
    from test_return_allocation_postgresql import processor

    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
    from src.core.uuid7 import new_uuid7
    from src.utils.timezone import timezone

    class Events:
        async def publish_to(self, *args, **kwargs):
            return False

    service = TransportService(
        p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420), event_publisher=Events()
    )
    current = await phase(p)
    operation_id = new_uuid7()
    data = {
        "transport_task_id": current.rotation_transport_task_id,
        "kind": "RACK_ROTATE",
        "outcome_revision": 1,
        "rack_id": "OFFLINE-RACK",
        "status": "FAILED" if unknown else "SUCCEEDED",
        "position_unknown": unknown,
    }
    if not unknown:
        data.update(final_position={"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}, arrival_face=face)
    else:
        data["failure_code"] = "OFFLINE_UNKNOWN"
    message = {"operation": "transport.task.resulted@v1", "operation_id": operation_id, "timestamp": 1, "data": data}
    ack = await service.record_callback(
        operation_id=operation_id,
        operation=message["operation"],
        message=message,
        payload=data,
        rejection_reason_code=None,
    )
    assert ack["code"] == "RECEIVED"
    assert await service.process_pending_evidence(100) == 1
    p.now = timezone.now_for_db()
    if publish:
        plugin = build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository())
        publisher = InstalledPluginTransportOutcomePublisher(
            p.sessions,
            (plugin,),
            queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
        )
        assert await service.publish_pending_outcomes(100, publisher) == 1
        assert await processor(p, scan=scan).process_batch() >= 1
    p.now = timezone.now_for_db()
    return service


async def test_success_before_ack_publishes_and_hands_off_next_member(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import select

    from src.app.execution.models import WmsConfirmation
    from src.app.transport.models import TransportTask

    p = bound
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    before = await phase(p)
    await rotation_result(p, batch, scan)
    observed = await phase(p)
    assert observed.rotation_result_evidence_id is not None
    await advance(p, batch=batch)
    async with p.sessions() as db:
        feed = await db.scalar(
            select(AutomaticPickingFeed).where(AutomaticPickingFeed.source_member_id == p.next_member_id)
        )
        assert feed is not None
        assert (feed.inbound_client_request_id, feed.round_started_at, feed.round_causal_token) == (
            before.inbound_client_request_id,
            before.round_started_at,
            before.round_causal_token,
        )
        assert feed.source_arrival_evidence_id == observed.rotation_result_evidence_id
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == feed.operation_id))
        assert request.request_payload["data"]["plan_revision"] == 2
        assert request.request_payload["data"]["rack_face"] == "B"
        assert (
            await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == before.rotation_transport_task_id)
            )
        ).status == "SUCCEEDED"
    assert (await phase(p)).archived_at is not None


async def next_feed(p):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import select

    async with p.sessions() as db:
        return await db.scalar(
            select(AutomaticPickingFeed).where(AutomaticPickingFeed.source_member_id == p.next_member_id)
        )


async def test_concurrent_creation_and_handoff_keep_one_identity(bound):
    import asyncio

    from sqlalchemy import func, select

    from src.app.transport.models import TransportTask

    p = bound
    batch, scan, _ = await rotation_application(p)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=batch))
    before = await phase(p)
    await rotation_result(p, batch, scan)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=batch))
    feed = await next_feed(p)
    assert feed is not None
    assert (await phase(p)).rotation_transport_task_id == before.rotation_transport_task_id
    async with p.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(TransportTask).where(TransportTask.kind == "RACK_ROTATE"))
            == 1
        )


@pytest.mark.parametrize("point", ["bind", "handoff"])
async def test_crash_rolls_back_creation_or_next_feed_and_recovers(bound, point):
    p = bound
    batch, scan, flow = await rotation_application(p)
    if point == "handoff":
        await advance(p, batch=batch)
        await rotation_result(p, batch, scan)
        owner, method = flow.scheduler, "create_in_session"
    else:
        owner, method = flow.repository, "bind"
    original = getattr(owner, method)

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline transaction crash")

    setattr(owner, method, crash)
    with pytest.raises(RuntimeError, match="offline transaction crash"):
        await advance(p, batch=batch)
    assert await next_feed(p) is None
    if point == "bind":
        from sqlalchemy import select

        from src.app.transport.models import TransportTask

        async with p.sessions() as db:
            assert not list((await db.scalars(select(TransportTask).where(TransportTask.kind == "RACK_ROTATE"))).all())
        assert (await phase(p)).rotation_transport_task_id is None
    else:
        assert (await phase(p)).archived_at is None
    setattr(owner, method, original)
    await advance(p, batch=batch)
    if point == "handoff":
        assert await next_feed(p) is not None
    else:
        assert (await phase(p)).rotation_transport_task_id is not None


@pytest.mark.parametrize(
    "blocked",
    [
        "stage_open",
        "inactive",
        "cancelled",
        "task_closed",
        "plan_blocked",
        "wrong_member",
        "wrong_position",
        "wrong_rack",
        "same_face",
    ],
)
async def test_creation_admission_uses_only_exact_current_source(bound, blocked):
    from dataclasses import replace

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack

    p = bound
    batch, _, _ = await rotation_application(p)
    current = await phase(p)
    async with p.sessions.begin() as db:
        if blocked == "stage_open":
            (await db.get(AutomaticPickingReturnMotion, current.id)).closed_at = None
        elif blocked == "inactive":
            (await db.get(type(p.line), p.line.id)).is_active = False
        elif blocked == "cancelled":
            (await db.get(PickingTaskBinSourceRack, p.next_member_id)).cancelled_evidence_id = p.task.issued_evidence_id
        elif blocked == "task_closed":
            (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        elif blocked == "plan_blocked":
            (await db.get(PickingTask, p.task.id)).plan_blocked_evidence_id = p.task.issued_evidence_id
    intent = p.rotations[0]
    if blocked == "wrong_member":
        p.rotations = (replace(intent, next_member_id=p.motion_sources[p.face_id].member_id),)
    elif blocked == "wrong_position":
        from wes_plugin_sdk import TransportRackPosition

        p.rotations = (replace(intent, position=TransportRackPosition("WRONG")),)
    elif blocked == "wrong_rack":
        p.rotations = (replace(intent, rack_id="WRONG"),)
    elif blocked == "same_face":
        p.rotations = (replace(intent, target_face="A"),)
    if blocked.startswith("wrong_") or blocked == "same_face":
        with pytest.raises(ValueError):
            await advance(p, batch=batch)
    else:
        # Keep 8E from closing an intentionally open stage in this admission test.
        batch.return_batch_flow = None
        await advance(p, batch=batch)
    assert (await phase(p)).rotation_transport_task_id is None


@pytest.mark.parametrize("state", ["unpublished", "unknown", "wrong_face"])
async def test_non_authoritative_success_cannot_create_next_feed(bound, state):
    p = bound
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(
        p,
        batch,
        scan,
        unknown=state == "unknown",
        face="A" if state == "wrong_face" else "B",
        publish=state != "unpublished",
    )
    await advance(p, batch=batch)
    assert await next_feed(p) is None
    assert (await phase(p)).archived_at is None


async def test_lost_notification_and_missing_reader_recover_original_result(bound):
    from types import SimpleNamespace as N

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository
    from test_return_allocation_postgresql import processor

    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    p = bound
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    original = (await phase(p)).rotation_transport_task_id
    service = await rotation_result(p, batch, scan, publish=False)
    scan.rack_motion_flow = None
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository()),),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    assert await service.publish_pending_outcomes(100, publisher) == 1
    await processor(p, scan=scan).process_batch()
    assert (await phase(p)).rotation_result_evidence_id is None
    flow = type(flow)(flow.returns, flow.creator, flow.scheduler, None, clock=lambda: p.now)
    batch.rack_motion_flow = scan.rack_motion_flow = flow
    # Expire the ordinary FactProcessor retry, not a second business recovery owner.
    from sqlalchemy import update

    from src.app.execution.models import InboundEvidence

    async with p.sessions.begin() as db:
        await db.execute(
            update(InboundEvidence)
            .where(InboundEvidence.transport_task_id == original, InboundEvidence.kind == "TRANSPORT_RESULT")
            .values(decision_next_attempt_at=None)
        )
    await processor(p, scan=scan).process_batch()
    flow.reader = None
    await advance(p, batch=batch)
    assert (await next_feed(p)).source_arrival_evidence_id is not None
    assert (await phase(p)).rotation_transport_task_id == original


async def test_released_window_and_later_revision_do_not_change_original_round(bound):
    from sqlalchemy import select

    from src.app.execution.models import TransportDecisionBinding
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = bound
    batch, scan, _ = await rotation_application(p)
    before = await phase(p)
    async with p.sessions.begin() as db:
        root = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == before.inbound_client_request_id
            )
        )
        root.window_released_at = p.now
        (await db.get(PickingTaskBinSourceRack, p.next_member_id)).plan_revision = 3
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    feed = await next_feed(p)
    assert feed.inbound_client_request_id == before.inbound_client_request_id
    assert feed.round_causal_token == before.round_causal_token


async def test_replayed_fact_after_handoff_does_not_reopen_stage(bound):
    from src.app.execution.models import InboundEvidence

    p = bound
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, current.rotation_result_evidence_id)
        assert (
            await flow.apply_transport_result_in_session(db, line=p.line, evidence=fact)
            == "SOURCE_ROTATION_OLD_OUTCOME"
        )
    feed = await next_feed(p)
    await advance(p, batch=batch)
    assert (await next_feed(p)).operation_id == feed.operation_id


async def finish_next_face(p):
    from types import SimpleNamespace as N

    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import select

    from src.app.execution.models import InboundEvidence, WmsConfirmation
    from src.utils.canonical_json import canonical_json_digest

    feed = await next_feed(p)
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingFeed, feed.id)
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == feed.operation_id))
        payload = {
            "operation_id": feed.operation_id,
            "code": "DECIDED",
            "timestamp": 1,
            "data": {"result": "RACK_FACE_DONE"},
        }
        end = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=request.operation + ":" + feed.operation_id,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            operation=request.operation,
            operation_id=feed.operation_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now,
            decision_digest="c" * 64,
        )
        db.add(end)
        await db.flush()
        row.phase, row.response_evidence_id = "FEED_COMPLETE", end.id
        request.status, request.response_evidence_id, request.completed_at = "COMPLETED", end.id, p.now
        p.motion_sources[feed.source_arrival_evidence_id] = N(
            root=feed.inbound_client_request_id, feed_id=feed.id, end_id=end.id
        )
    p.faces = [(feed.source_arrival_evidence_id, "OFFLINE-RACK", "B")]
    p.rotations = ()
    return feed


async def test_next_stage_recovers_after_original_ingress_and_closed_history_deleted(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy import delete, select

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportMember, TransportTask

    p = bound
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    old = await phase(p)
    original = p.motion_sources[p.face_id]
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    feed = await finish_next_face(p)
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingReturnMotion, old.id))
        await db.delete(await db.get(AutomaticPickingFeed, original.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == original.end_id))
        await db.delete(await db.get(InboundEvidence, p.face_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == original.transport_id))
        (await db.get(TransportDecisionBinding, original.root_id)).window_released_at = p.now
        await db.flush()
        await db.delete(await db.get(TransportDecisionBinding, original.root_id))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == original.transport_id))
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.id != old.id
    assert current.source_feed_id == feed.id and current.closed_at is not None
    assert current.inbound_client_request_id == old.inbound_client_request_id
    assert current.round_started_at == old.round_started_at
    assert current.round_causal_token == old.round_causal_token
    assert await advance(p, batch=batch) == 0


@pytest.mark.parametrize("takeover", ["pending", "same_line", "other_line", "after_unknown"])
async def test_same_rack_takeover_never_applies_old_rotation_to_new_round(bound, takeover):
    from motion_binding_support import rack_action
    from sqlalchemy import select

    from src.app.execution.models import InboundEvidence, PositionProjection
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan, unknown=takeover == "after_unknown")
    async with p.sessions.begin() as db:
        line_id = p.line.id
        if takeover == "other_line":
            line = type(p.line)(
                line_name="other-offline",
                line_code="OTHER-OFFLINE",
                line_type=p.line.line_type,
                position_bindings=p.line.position_bindings,
            )
            db.add(line)
            await db.flush()
            line_id = line.id
        from src.app.execution.models import TransportDecisionBinding

        original = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == (await phase(p)).inbound_client_request_id
            )
        )
        original.window_released_at = p.now
        await db.flush()
        task, binding = await rack_action(
            db, p, "OFFLINE-RACK", "B", status="PENDING" if takeover == "pending" else "SUCCEEDED", line_id=line_id
        )
        if takeover != "pending":
            payload = {
                "transport_task_id": task.transport_task_id,
                "client_request_id": task.client_request_id,
                "status": "SUCCEEDED",
                "outcome_version": 1,
            }
            fact = InboundEvidence(
                kind="TRANSPORT_RESULT",
                source_identity="offline-takeover:" + task.transport_task_id,
                decision_digest="c" * 64,
                transport_task_id=task.transport_task_id,
                workline_id=line_id,
                picking_task_id=p.task.id,
                normalized_payload=payload,
                payload_digest=canonical_json_digest(payload),
                apply_status="APPLIED",
                published_at=p.now,
                received_at=p.now,
            )
            db.add(fact)
            projection = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
                )
            )
            projection.position_unknown = False
            projection.arrival_face = "B"
            projection.position_json = {"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}
            projection.workline_id, projection.source_transport_task_id, projection.source_causal_token = (
                line_id,
                task.transport_task_id,
                binding.causal_token,
            )
    await advance(p, batch=batch)
    assert await next_feed(p) is None
    assert ((await phase(p)).archived_at is not None) == (takeover != "pending")


@pytest.mark.parametrize("rack", ["OFFLINE-RACK", "INDEPENDENT-RACK"])
async def test_unknown_direct_dependency_blocks_rotation_but_other_rack_does_not(bound, rack):
    from motion_binding_support import rack_action
    from sqlalchemy import select

    from src.app.transport.models import TransportTask

    p = bound
    batch, _, _ = await rotation_application(p)
    async with p.sessions.begin() as db:
        await rack_action(db, p, rack, "A", root=object(), status="PENDING")
    await advance(p, batch=batch)
    assert ((await phase(p)).rotation_transport_task_id is not None) == (rack != "OFFLINE-RACK")


async def test_no_executing_task_still_observes_original_result_without_new_business(bound):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = bound
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await rotation_result(p, batch, scan)
    flow.reader = None
    await advance(p, batch=batch)
    assert (await phase(p)).rotation_result_evidence_id is not None
    assert await next_feed(p) is None
    assert (await phase(p)).archived_at is not None


async def test_normal_batch_cannot_bypass_handoff_and_later_revision_inherits_current_round(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import select

    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = bound
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan)
    async with p.sessions.begin() as db:
        # Result has been published, but the Motion still owns the handoff.
        assert not await batch.feeds.source_ready(db, p.line, await db.get(PickingTaskBinSourceRack, p.next_member_id))
    await advance(p, batch=batch)
    first = await next_feed(p)
    async with p.sessions.begin() as db:
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=4,
            rack_id="OFFLINE-RACK",
            rack_face="B",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        member_id = member.id
        assert await batch.feeds.source_ready(db, p.line, member)
        batch.batch_scheduler = flow.scheduler
        await batch.advance_in_session(db, p.line, p.task)
    async with p.sessions() as db:
        later = await db.scalar(select(AutomaticPickingFeed).where(AutomaticPickingFeed.source_member_id == member_id))
        assert later.inbound_client_request_id == first.inbound_client_request_id
        assert later.source_arrival_evidence_id == first.source_arrival_evidence_id


async def test_rotation_models_match_schema(bound):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare: (
                    kind != "table" or name in {"automatic_picking_feeds", "automatic_picking_return_motions"}
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingFeed.metadata)

    async with bound.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []


async def test_migration_preserves_8f_and_refuses_losing_rotation(bound, database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = bound
    batch, _, _ = await rotation_application(p)
    before = await phase(p)
    url, _ = database
    run_alembic("downgrade", "20d588a3a234", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    current = await phase(p)
    assert current.inbound_client_request_id == before.inbound_client_request_id
    assert current.rotation_transport_task_id is None
    await advance(p, batch=batch)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "20d588a3a234", database_url=url)
    assert "cannot discard automatic source rotation handoff" in error.value.stderr


async def test_partial_feed_round_is_rejected_by_database(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    p = bound
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            await db.execute(
                update(AutomaticPickingFeed)
                .where(AutomaticPickingFeed.id == p.motion_sources[p.face_id].feed_id)
                .values(inbound_client_request_id="partial")
            )


@pytest.mark.parametrize("drift", ["position", "face", "owner"])
async def test_frozen_action_or_owner_cannot_drift(bound, drift):
    from dataclasses import replace

    from sqlalchemy import select
    from wes_plugin_sdk import TransportRackPosition

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportTask

    p = bound
    batch, _, _ = await rotation_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    if drift == "owner":
        async with p.sessions.begin() as db:
            task = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == current.rotation_transport_task_id)
            )
            binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
            binding.picking_task_id = None
    else:
        p.rotations = (
            replace(
                p.rotations[0],
                **({"position": TransportRackPosition("OTHER")} if drift == "position" else {"target_face": "C"}),
            ),
        )
    with pytest.raises(ValueError):
        await advance(p, batch=batch)
    assert (await phase(p)).rotation_transport_task_id == current.rotation_transport_task_id


async def test_publication_crash_rolls_back_plugin_fact_and_reuses_original_outcome(bound):
    from datetime import timedelta
    from types import SimpleNamespace as N

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository
    from sqlalchemy import select
    from test_return_allocation_postgresql import processor

    from src.app.execution.models import InboundEvidence
    from src.app.transport.models import TransportTask
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
    from src.utils.timezone import timezone

    p = bound
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    original_id = (await phase(p)).rotation_transport_task_id
    service = await rotation_result(p, batch, scan, publish=False)
    plugin = build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository())
    original = plugin.transport_outcome_publisher.publish

    async def crash(db, outcome):
        await original(db, outcome)
        raise RuntimeError("offline publication crash")

    plugin.transport_outcome_publisher.publish = crash
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (plugin,),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    assert await service.publish_pending_outcomes(100, publisher) == 0
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == original_id))
        assert task.published_outcome_version == 0
        assert not list(
            (await db.scalars(select(InboundEvidence).where(InboundEvidence.transport_task_id == original_id))).all()
        )
        task.outcome_claim_until = timezone.now_for_db() - timedelta(seconds=1)
    plugin.transport_outcome_publisher.publish = original
    assert await service.publish_pending_outcomes(100, publisher) == 1
    await processor(p, scan=scan).process_batch()
    p.now = timezone.now_for_db()
    flow.reader = None
    await advance(p, batch=batch)
    assert (await next_feed(p)).source_arrival_evidence_id is not None
    assert (await phase(p)).rotation_transport_task_id == original_id
