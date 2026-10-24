"""普通来源架真实 DB 候选接续；只持久创建，不进行外部派发。"""

import asyncio

import pytest
from sqlalchemy import select
from test_return_allocation_postgresql import advance, application, arrived, database, observed, pending
from test_return_before_motion_postgresql import phase, qualified
from test_return_motion_binding_postgresql import empty_candidates
from test_source_rack_departure_decision_postgresql import decided, requests

from src.app.execution.models import WmsConfirmation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def candidate_application(p):
    from automatic_picking.application.source_rack_candidates import (
        SourceRackDepartureReader,
        SourceRackFaceReader,
        SourceRackMotionReader,
    )
    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow

    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService

    batch, scan = application(p)
    returns = batch.return_batch_flow
    returns.face_reader = SourceRackFaceReader()
    returns.motion_reader = SourceRackMotionReader()
    p.departure_service = returns.creator._transport
    flow = SourceRackDepartureFlow(
        returns,
        ReliableRackTransportCreator(p.departure_service, inbound_window=RackInboundWindowService()),
        SourceRackDepartureReader(),
        scheduler=RackDepartureScheduler(WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService())),
        clock=lambda: p.now,
    )
    batch.rack_departure_flow = scan.rack_departure_flow = flow
    return batch, scan, flow


async def test_real_facts_discover_bind_and_request_without_explicit_candidates(qualified):
    p = qualified
    await empty_candidates(p)
    batch, _, _ = candidate_application(p)
    assert await phase(p) is None
    await advance(p, batch=batch)
    current = await phase(p)
    ctx = p.motion_sources[p.face_id]
    assert (current.arrival_evidence_id, current.source_feed_id, current.service_end_evidence_id) == (
        p.face_id,
        ctx.feed_id,
        ctx.end_id,
    )
    assert current.inbound_client_request_id == ctx.root
    assert current.closed_at is not None and current.departure_operation_id is not None
    assert current.departure_transport_task_id is None
    await advance(p, batch=candidate_application(p)[0])
    saved = await requests(p)
    assert len(saved) == 1 and saved[0].operation_id == current.departure_operation_id
    assert saved[0].request_payload["data"] == {
        "task_id": None,
        "rack_id": "OFFLINE-RACK",
        "current_location": {"type": "RACK_POSITION", "location_code": "OFFLINE-FIVE"},
        "current_face": "A",
    }


async def test_concurrent_real_discovery_freezes_one_stage_and_request(qualified):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    p = qualified
    await empty_candidates(p)
    await asyncio.gather(*(advance(p, batch=candidate_application(p)[0]) for _ in range(6)))
    async with p.sessions() as db:
        rows = list((await db.scalars(select(AutomaticPickingReturnMotion))).all())
    assert len(rows) == len(await requests(p)) == 1
    assert rows[0].departure_operation_id == (await requests(p))[0].operation_id


@pytest.mark.parametrize("crash_at", ["binding", "request"])
async def test_discovery_handoff_and_first_request_rollback_then_restart(qualified, crash_at):
    from automatic_picking.application.return_motion_repository import ReturnMotionRepository

    p = qualified
    await empty_candidates(p)
    batch, _, flow = candidate_application(p)
    if crash_at == "binding":

        class Crash(ReturnMotionRepository):
            async def bind_source(self, db, current, intent, root):
                await super().bind_source(db, current, intent, root)
                raise RuntimeError("handoff crash")

        batch.return_batch_flow.motions = Crash()
    else:
        original = flow.scheduler.create_in_session

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("handoff crash")

        flow.scheduler.create_in_session = crash
    with pytest.raises(RuntimeError, match="handoff crash"):
        await advance(p, batch=batch)
    assert await phase(p) is None and await requests(p) == []
    await advance(p, batch=candidate_application(p)[0])
    assert (await phase(p)).departure_operation_id == (await requests(p))[0].operation_id


@pytest.mark.parametrize(
    "fault", ["missing_feed", "unfinished", "unpublished", "unknown_position", "unpublished_arrival"]
)
async def test_absence_or_uncertain_facts_never_authorize_departure(qualified, fault):
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    from src.app.execution.models import InboundEvidence, PositionProjection

    p = qualified
    await empty_candidates(p)
    ctx = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        if fault == "missing_feed":
            await db.delete(await db.get(AutomaticPickingFeed, ctx.feed_id))
        elif fault == "unfinished":
            (await db.get(AutomaticPickingFeed, ctx.feed_id)).phase = "FEEDING"
        elif fault == "unpublished":
            (await db.get(InboundEvidence, ctx.end_id)).published_at = None
        elif fault == "unpublished_arrival":
            (await db.get(InboundEvidence, p.face_id)).published_at = None
        else:
            projection = await db.scalar(
                select(PositionProjection).where(PositionProjection.object_id == "OFFLINE-RACK")
            )
            projection.position_unknown = True
    await advance(p, batch=candidate_application(p)[0])
    assert await phase(p) is None and await requests(p) == []


async def add_source_member(p, face="B", *, cancelled=False):
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    async with p.sessions.begin() as db:
        row = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            rack_id="OFFLINE-RACK",
            rack_face=face,
            plan_revision=99,
            source_evidence_id=p.task.issued_evidence_id,
            cancelled_evidence_id=p.task.issued_evidence_id if cancelled else None,
        )
        db.add(row)
        await db.flush()
        return row.id


@pytest.mark.parametrize("cancelled", [False, True])
async def test_existing_next_service_face_is_not_inferred_finished(qualified, cancelled):
    p = qualified
    await empty_candidates(p)
    await add_source_member(p, cancelled=cancelled)
    await advance(p, batch=candidate_application(p)[0])
    assert (await phase(p)).closed_at is not None
    assert bool(await requests(p)) is cancelled


async def test_future_revision_does_not_rebind_or_extend_frozen_motion(qualified):
    p = qualified
    await empty_candidates(p)
    batch, _, _ = candidate_application(p)
    batch.rack_departure_flow = None
    await advance(p, batch=batch)
    original = await phase(p)
    await add_source_member(p)
    await advance(p, batch=candidate_application(p)[0])
    current = await phase(p)
    assert current.id == original.id and current.source_feed_id == original.source_feed_id
    assert current.departure_operation_id is not None


async def test_released_window_and_no_executing_task_keep_first_discovery_owner(qualified):
    from src.app.execution.models import TransportDecisionBinding
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = qualified
    await empty_candidates(p)
    ctx = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, ctx.root_id)).window_released_at = p.now
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await advance(p, batch=candidate_application(p)[0])
    current = await phase(p)
    assert current.inbound_client_request_id == ctx.root and current.departure_operation_id is not None


@pytest.mark.parametrize("same_rack", [True, False])
async def test_unacked_same_object_action_blocks_but_independent_object_does_not(qualified, same_rack):
    from motion_binding_support import rack_action

    p = qualified
    await empty_candidates(p)
    async with p.sessions.begin() as db:
        await rack_action(db, p, "OFFLINE-RACK" if same_rack else "INDEPENDENT", "B", root=object(), status="PENDING")
    await advance(p, batch=candidate_application(p)[0])
    assert bool(await requests(p)) is (not same_rack)


@pytest.mark.parametrize("before_request", [True, False])
async def test_restart_after_handoff_needs_no_closed_feed_or_confirmation(qualified, before_request):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import delete

    p = qualified
    await empty_candidates(p)
    batch, _, _ = candidate_application(p)
    if before_request:
        batch.rack_departure_flow = None
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingFeed, current.source_feed_id))
        await db.execute(
            delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == current.service_end_evidence_id)
        )
    await advance(p, batch=candidate_application(p)[0])
    assert (await phase(p)).id == current.id and len(await requests(p)) == 1
    if not before_request:
        assert (await phase(p)).departure_operation_id == current.departure_operation_id


async def test_ready_after_real_candidate_handoff_creates_original_ctu03_once(qualified):
    from src.app.execution.models import PositionProjection
    from src.app.transport.models import TransportTask

    p = qualified
    await empty_candidates(p)
    async with p.sessions.begin() as db:
        projection = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "OFFLINE-RACK"))
        projection.source_effect_phase = "FINAL_RESULT"
    batch, _, _ = candidate_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    await decided(p, "READY")
    await advance(p, batch=candidate_application(p)[0])
    task_id = (await phase(p)).departure_transport_task_id
    assert task_id is not None
    await advance(p, batch=candidate_application(p)[0])
    async with p.sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        assert task.request_json["rcs_template_id"] == "CTU03" and task.status == "PENDING"
    assert len(await requests(p)) == 1 and (await phase(p)).departure_operation_id == current.departure_operation_id


@pytest.mark.parametrize("other_line", [False, True])
async def test_real_reader_does_not_recreate_old_wait_after_authoritative_takeover(qualified, other_line):
    from test_source_rack_departure_decision_postgresql import given_takeover

    p = qualified
    await empty_candidates(p)
    await advance(p, batch=candidate_application(p)[0])
    original = await phase(p)
    await decided(p)
    await given_takeover(p, other_line=other_line)
    for _ in range(2):
        await advance(p, batch=candidate_application(p)[0])
    current = await phase(p)
    assert current.id == original.id and current.archived_at is not None
    assert len(await requests(p)) == 1 and current.departure_transport_task_id is None


async def test_final_rotated_face_uses_existing_stage_without_requiring_new_feed(qualified):
    from test_source_rack_motion_postgresql import rotation_application, rotation_result

    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
    from src.utils.timezone import timezone

    p = qualified
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "CANCELLED"
        (await db.get(PickingTaskBinSourceRack, p.next_member_id)).cancelled_evidence_id = p.task.issued_evidence_id
    p.now = timezone.now_for_db()
    previous = await phase(p)
    await advance(p, batch=candidate_application(p)[0])
    current = await phase(p)
    assert current.id == previous.id and current.inbound_client_request_id == previous.inbound_client_request_id
    assert len(await requests(p)) == 1
    assert (await requests(p))[0].request_payload["data"]["current_face"] == "B"


@pytest.mark.parametrize("unknown", [False, True])
async def test_nonempty_feed_discovers_exact_published_final_chunk(qualified, unknown):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from motion_binding_support import rack_action

    from src.app.execution.models import InboundEvidence
    from src.app.transport.models import TransportMember
    from src.core.uuid7 import new_uuid7
    from src.utils.canonical_json import canonical_json_digest

    p = qualified
    await empty_candidates(p)
    ctx = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        feed = await db.get(AutomaticPickingFeed, ctx.feed_id)
        response = await db.get(InboundEvidence, ctx.end_id)
        payload = {
            **response.normalized_payload,
            "data": {
                "result": "READY",
                "bins": [
                    {
                        "bin_code": "A000000001",
                        "source_locator": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": "OFFLINE-RACK",
                            "rack_face": "A",
                            "slot_id": "S1",
                        },
                    }
                ],
            },
        }
        response.normalized_payload, response.payload_digest = payload, canonical_json_digest(payload)
        task, binding = await rack_action(db, p, "A000000001", "A", root=object(), kind="BIN_MOVE")
        binding.step, binding.resource_fence_id, binding.source_evidence_id = (
            "AUTOMATIC_PICKING_FEED",
            feed.operation_id,
            response.id,
        )
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
        )
        member.object_type, member.arrival_face = "BIN", None
        member.source_json = {"kind": "RACK_BIN_SLOT", "rack_id": "OFFLINE-RACK", "rack_face": "A", "slot_id": "S1"}
        member.target_json = {"kind": "HANDOFF_POSITION", "location_code": "OFFLINE-INLET"}
        member.final_position_json, member.position_unknown = member.target_json, unknown
        result = {
            "transport_task_id": task.transport_task_id,
            "client_request_id": task.client_request_id,
            "kind": "BIN_MOVE",
            "status": "SUCCEEDED",
            "outcome_version": 1,
        }
        end = InboundEvidence(
            kind="TRANSPORT_RESULT",
            source_identity="transport:" + new_uuid7(),
            transport_task_id=task.transport_task_id,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            normalized_payload=result,
            payload_digest=canonical_json_digest(result),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now,
            decision_digest="d" * 64,
        )
        db.add(end)
        await db.flush()
        end_id = end.id
        feed.offset, feed.transport_task_id = 1, task.transport_task_id
    await advance(p, batch=candidate_application(p)[0])
    if unknown:
        assert await phase(p) is None and await requests(p) == []
    else:
        assert (await phase(p)).service_end_evidence_id == end_id
        assert len(await requests(p)) == 1


async def test_new_round_ignores_complete_old_feed_and_released_ingress(qualified):
    from datetime import timedelta

    from motion_binding_support import source_facts
    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding

    p = qualified
    await empty_candidates(p)
    old = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, old.root_id)).window_released_at = p.now
    p.now += timedelta(seconds=60)
    following = await new_face(p)
    ctx = await source_facts(p, following)
    await advance(p, batch=candidate_application(p)[0])
    current = await phase(p)
    assert current.inbound_client_request_id == ctx.root != old.root
    assert current.source_feed_id == ctx.feed_id != old.feed_id
    assert len(await requests(p)) == 1


async def test_existing_fifo_obligation_is_resumed_before_departure(qualified):
    from test_return_allocation_postgresql import processor, result

    p = qualified
    batch, scan, _ = candidate_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.allocation_id is not None and current.closed_at is None
    assert await requests(p) == []
    await result(p, result="NO_BATCH")
    assert await processor(p, scan=scan).process_batch() == 1
    await advance(p, batch=candidate_application(p)[0])
    assert (await phase(p)).closed_at is not None and len(await requests(p)) == 1


async def test_other_face_missing_completion_fact_cannot_hide_current_service(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    from src.core.uuid7 import new_uuid7

    p = qualified
    await empty_candidates(p)
    identity = await add_source_member(p)
    async with p.sessions.begin() as db:
        db.add(
            AutomaticPickingFeed(
                workline_id=p.line.id,
                picking_task_id=p.task.id,
                source_member_id=identity,
                operation_id=new_uuid7(),
                phase="FEED_COMPLETE",
                inbound_client_request_id="old-round",
                round_started_at=p.now,
                round_causal_token=1,
                source_arrival_evidence_id=p.face_id,
            )
        )
    await advance(p, batch=candidate_application(p)[0])
    assert await requests(p) == []


async def test_accepted_unserved_same_face_member_prevents_premature_handoff(qualified):
    p = qualified
    await empty_candidates(p)
    await add_source_member(p, face="A")
    await advance(p, batch=candidate_application(p)[0])
    assert await phase(p) is None and await requests(p) == []


async def test_completed_member_from_prior_round_does_not_reopen_service_in_new_round(qualified):
    from datetime import timedelta

    from motion_binding_support import source_facts
    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding

    p = qualified
    await empty_candidates(p)
    original = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, original.root_id)).window_released_at = p.now
    p.now += timedelta(seconds=60)
    old_face = await new_face(p, face="B")
    completed = await source_facts(p, old_face, revision=2)
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, completed.root_id)).window_released_at = p.now
    p.now += timedelta(seconds=60)
    new_arrival = await new_face(p, face="A")
    current = await source_facts(p, new_arrival, revision=3)
    await advance(p, batch=candidate_application(p)[0])
    assert (await phase(p)).inbound_client_request_id == current.root
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("clean_current_feed", [False, True])
async def test_multiface_handoff_survives_closed_feed_and_ingress_cleanup(qualified, database, clean_current_feed):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy import delete
    from test_source_rack_motion_postgresql import finish_next_face, rotation_application, rotation_result

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding
    from src.app.transport.models import TransportMember, TransportTask

    p = qualified
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    old = await phase(p)
    source = p.motion_sources[p.face_id]
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    feed = await finish_next_face(p)
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingReturnMotion, old.id))
        await db.delete(await db.get(AutomaticPickingFeed, source.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == source.end_id))
        await db.delete(await db.get(InboundEvidence, p.face_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == source.transport_id))
        (await db.get(TransportDecisionBinding, source.root_id)).window_released_at = p.now
        await db.flush()
        await db.delete(await db.get(TransportDecisionBinding, source.root_id))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == source.transport_id))
    discovery, _, _ = candidate_application(p)
    discovery.rack_departure_flow = None
    await advance(p, batch=discovery)
    current = await phase(p)
    assert current.source_feed_id == feed.id and current.inbound_client_request_id == old.inbound_client_request_id
    assert set(current.completed_source_member_ids) == {source.member_id, feed.source_member_id}
    if clean_current_feed:
        async with p.sessions.begin() as db:
            await db.delete(await db.get(AutomaticPickingFeed, feed.id))
            await db.execute(
                delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == current.service_end_evidence_id)
            )
    await advance(p, batch=candidate_application(p)[0])
    assert len(await requests(p)) == 1
    assert (await requests(p))[0].request_payload["data"]["current_face"] == "B"

    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    url, _ = database
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "de97e0860fd5", database_url=url)
    assert "cannot discard automatic source service completion handoff" in error.value.stderr


async def test_service_fact_migration_seeds_existing_progress_and_matches_models(qualified, database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from tests.support.postgresql_heavy import run_alembic

    p = qualified
    await empty_candidates(p)
    batch, _, _ = candidate_application(p)
    batch.rack_departure_flow = None
    await advance(p, batch=batch)
    original = await phase(p)
    url, _ = database
    await p.sessions.kw["bind"].dispose()
    run_alembic("downgrade", "de97e0860fd5", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    current = await phase(p)
    assert current.id == original.id and current.completed_source_member_ids == [p.motion_sources[p.face_id].member_id]
    async with p.sessions() as db:
        feed = await db.get(AutomaticPickingFeed, current.source_feed_id)
        assert feed.completed_source_member_ids == current.completed_source_member_ids

        def compare(connection):
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                    "include_object": lambda _obj, name, kind, _reflected, _compare: (
                        kind != "table"
                        or name
                        in {
                            "automatic_picking_feeds",
                            "automatic_picking_return_motions",
                        }
                    ),
                },
            )
            return compare_metadata(context, AutomaticPickingReturnMotion.metadata)

        assert await (await db.connection()).run_sync(compare) == []
    await advance(p, batch=candidate_application(p)[0])
    assert len(await requests(p)) == 1


async def test_inherited_same_face_completion_does_not_require_deleted_feed(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from motion_binding_support import source_facts
    from sqlalchemy import delete

    p = qualified
    await empty_candidates(p)
    old = p.motion_sources[p.face_id]
    following = await source_facts(p, p.faces[0], root_client=old.root, reuse_arrival=True, revision=2)
    async with p.sessions.begin() as db:
        feed = await db.get(AutomaticPickingFeed, following.feed_id)
        feed.completed_source_member_ids = [old.member_id]
        await db.delete(await db.get(AutomaticPickingFeed, old.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == old.end_id))
    await advance(p, batch=candidate_application(p)[0])
    assert (await phase(p)).source_feed_id == following.feed_id and len(await requests(p)) == 1


async def test_completed_next_member_never_creates_redundant_rotation(qualified):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_source_rack_motion_postgresql import rotation_application

    p = qualified
    batch, _, _ = await rotation_application(p)
    current = await phase(p)
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturnMotion, current.id)
        row.completed_source_member_ids = [*row.completed_source_member_ids, p.next_member_id]
    await advance(p, batch=batch)
    assert (await phase(p)).rotation_transport_task_id is None


async def test_unpublished_sibling_completion_does_not_enter_durable_service_facts(qualified):
    from motion_binding_support import source_facts

    from src.app.execution.models import InboundEvidence

    p = qualified
    await empty_candidates(p)
    old = p.motion_sources[p.face_id]
    sibling = await source_facts(p, p.faces[0], root_client=old.root, reuse_arrival=True, revision=2)
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, sibling.end_id)).published_at = None
    await advance(p, batch=candidate_application(p)[0])
    assert await phase(p) is None and await requests(p) == []


async def test_task_advance_preserves_completed_member_after_rotation_and_feed_cleanup(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.source_rack_motion import MatchedSourceRackRotation
    from sqlalchemy import delete
    from test_source_rack_motion_postgresql import finish_next_face, rotation_application, rotation_result
    from wes_plugin_sdk import TransportRackPosition

    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
    from src.app.workline.models import WorkLine

    p = qualified
    original = p.motion_sources[p.face_id]
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    await finish_next_face(p)
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=3,
            rack_id="OFFLINE-RACK",
            rack_face="A",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        next_id = member.id
    p.rotations = (
        MatchedSourceRackRotation(current.id, next_id, "OFFLINE-RACK", TransportRackPosition("OFFLINE-FIVE"), "A"),
    )
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan, face="A")
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        later = await db.scalar(select(AutomaticPickingFeed).where(AutomaticPickingFeed.source_member_id == next_id))
        assert original.member_id in later.completed_source_member_ids
        await db.delete(await db.get(AutomaticPickingFeed, original.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == original.end_id))
        await db.flush()
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        batch.batch_scheduler = flow.scheduler
        assert await batch.advance_in_session(db, line, p.task) == 0
        assert await batch.feeds.by_member(db, original.member_id) is None
        # 新 revision 仍可在同一到位下接续。
        fresh = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=4,
            rack_id="OFFLINE-RACK",
            rack_face="A",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(fresh)
        await db.flush()
        assert await batch.advance_in_session(db, line, p.task) == 1
        assert (
            await batch.feeds.by_member(db, fresh.id)
        ).completed_source_member_ids == later.completed_source_member_ids


async def test_upgrade_refuses_active_bound_stage_with_cleaned_source_feed(qualified, database):
    import subprocess

    from sqlalchemy import text

    from tests.support.postgresql_heavy import run_alembic

    p = qualified
    await empty_candidates(p)
    batch, _, _ = candidate_application(p)
    batch.rack_departure_flow = None
    await advance(p, batch=batch)
    current = await phase(p)
    url, _ = database
    await p.sessions.kw["bind"].dispose()
    run_alembic("downgrade", "de97e0860fd5", database_url=url)
    async with p.sessions.begin() as db:
        await db.execute(
            text("DELETE FROM wes_biz.automatic_picking_feeds WHERE id = :id"), {"id": current.source_feed_id}
        )
    await p.sessions.kw["bind"].dispose()
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("upgrade", "head", database_url=url)
    assert "cannot recover automatic source service completion from cleaned feed" in error.value.stderr


async def test_task_advance_does_not_reopen_ctu01_service_handed_to_waiting_stage(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    from src.app.workline.models import WorkLine

    p = qualified
    batch, _, _ = candidate_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    original = p.motion_sources[p.face_id]
    assert current.closed_at is None and original.member_id in current.completed_source_member_ids
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingFeed, original.feed_id))
        await db.flush()
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert await batch.advance_in_session(db, line, p.task) == 0
        assert await batch.feeds.by_member(db, original.member_id) is None


@pytest.mark.parametrize("bound_next_stage", [False, True])
@pytest.mark.parametrize("clean_predecessor", [False, True])
async def test_legacy_multiface_upgrade_requires_recoverable_predecessor(
    qualified, database, bound_next_stage, clean_predecessor
):
    import subprocess

    from sqlalchemy import text
    from test_source_rack_motion_postgresql import finish_next_face, rotation_application, rotation_result

    from tests.support.postgresql_heavy import run_alembic

    p = qualified
    original = p.motion_sources[p.face_id]
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    old = await phase(p)
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    if bound_next_stage:
        await finish_next_face(p)
        await advance(p, batch=batch)
    url, _ = database
    await p.sessions.kw["bind"].dispose()
    run_alembic("downgrade", "de97e0860fd5", database_url=url)
    async with p.sessions.begin() as db:
        await db.execute(text("DELETE FROM wes_biz.automatic_picking_return_motions WHERE id = :id"), {"id": old.id})
        if clean_predecessor:
            await db.execute(
                text("DELETE FROM wes_biz.automatic_picking_feeds WHERE id = :id"), {"id": original.feed_id}
            )
    await p.sessions.kw["bind"].dispose()
    if clean_predecessor:
        with pytest.raises(subprocess.CalledProcessError) as error:
            run_alembic("upgrade", "head", database_url=url)
        assert "cannot recover automatic source service completion from cleaned feed" in error.value.stderr
    else:
        from automatic_picking.application.feed_model import AutomaticPickingFeed

        run_alembic("upgrade", "head", database_url=url)
        async with p.sessions() as db:
            feed = await db.scalar(
                select(AutomaticPickingFeed).where(AutomaticPickingFeed.source_member_id == p.next_member_id)
            )
            assert original.member_id in feed.completed_source_member_ids


async def test_bound_motion_completion_precedes_retained_same_face_feed(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from test_source_rack_motion_postgresql import finish_next_face, rotation_application, rotation_result

    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
    from src.app.workline.models import WorkLine

    p = qualified
    batch, scan, flow = await rotation_application(p)
    await advance(p, batch=batch)
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    first = await finish_next_face(p)
    async with p.sessions.begin() as db:
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=3,
            rack_id="OFFLINE-RACK",
            rack_face="B",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        p.next_member_id = member.id
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        batch.batch_scheduler = flow.scheduler
        assert await batch.advance_in_session(db, line, p.task) == 1
    second = await finish_next_face(p)
    batch, _, _ = candidate_application(p)
    batch.rack_departure_flow = None
    await advance(p, batch=batch)
    current = await phase(p)
    assert {first.source_member_id, second.source_member_id} <= set(current.completed_source_member_ids)
    assert current.departure_operation_id is None
    async with p.sessions.begin() as db:
        assert second.source_member_id not in (await db.get(AutomaticPickingFeed, first.id)).completed_source_member_ids
        await db.delete(await db.get(AutomaticPickingFeed, second.id))
        await db.flush()
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        batch.batch_scheduler = flow.scheduler
        assert await batch.advance_in_session(db, line, p.task) == 0
        assert await batch.feeds.by_member(db, second.source_member_id) is None
