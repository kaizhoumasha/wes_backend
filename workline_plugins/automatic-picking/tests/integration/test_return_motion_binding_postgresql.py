"""8F 精确来源结束与原进场绑定；所有输入为离线权威事实。"""

import pytest
import pytest_asyncio
from automatic_picking.application.return_allocation_flow import MatchedReturnFace, MatchedReturnMotion
from motion_binding_support import rack_action, source_facts
from sqlalchemy import delete, select
from test_return_allocation_postgresql import advance, application, arrived, database, observed, pending, qualified
from test_return_before_motion_postgresql import MotionReader, motion_application, phase

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_unbound_source_cannot_create_motion_phase(bound):
    p = bound
    batch, _ = application(p)

    class UnboundReader:
        async def read_in_session(self, db, *, workline_id):
            return (
                MatchedReturnMotion(
                    MatchedReturnFace(p.face_id, workline_id, "OFFLINE-RACK", "A"),
                    p.face_id,
                    "missing-explicit-root",
                    p.feed.id,
                    p.face_id,
                ),
            )

    batch.return_batch_flow.motion_reader = UnboundReader()
    await advance(p, batch=batch)
    assert await phase(p) is None


@pytest_asyncio.fixture
async def bound(qualified):
    await source_facts(qualified, qualified.faces[0])
    return qualified


async def current_source(p, batch):
    from src.app.workline.models import WorkLine

    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        return await batch.return_batch_flow.motions.current_source(db, line, await phase(p))


async def empty_candidates(p):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).archived_at = p.now


async def test_exact_source_end_and_original_round_are_frozen(bound):
    from src.app.execution.models import TransportDecisionBinding

    p = bound
    ctx = p.motion_sources[p.face_id]
    batch, _ = motion_application(p)
    assert await advance(p, batch=batch) > 0
    saved = await phase(p)
    async with p.sessions() as db:
        root = await db.get(TransportDecisionBinding, ctx.root_id)
        assert saved.inbound_client_request_id == root.client_request_id
        assert saved.round_started_at == root.created_at and saved.round_causal_token == root.causal_token
    assert (saved.source_feed_id, saved.service_end_evidence_id) == (ctx.feed_id, ctx.end_id)
    assert await current_source(p, batch)


@pytest.mark.parametrize("fault", ["unfinished", "unpublished", "wrong_evidence", "wrong_root"])
async def test_source_end_must_be_exact_and_published(bound, fault):
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding

    p = bound
    ctx = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        if fault == "unfinished":
            (await db.get(AutomaticPickingFeed, ctx.feed_id)).phase = "FEEDING"
        elif fault == "unpublished":
            (await db.get(InboundEvidence, ctx.end_id)).published_at = None
        elif fault == "wrong_evidence":
            ctx.end_id = p.face_id
        else:
            (await db.get(TransportDecisionBinding, ctx.root_id)).resource_fence_id = "OTHER"
    batch, _ = motion_application(p)
    if fault in {"wrong_evidence", "wrong_root"}:
        with pytest.raises(ValueError):
            await advance(p, batch=batch)
    else:
        await advance(p, batch=batch)
    assert await phase(p) is None


async def test_rotation_keeps_original_round_and_all_faces_direct_obligations(bound):
    from test_return_allocation_postgresql import new_face

    p = bound
    root = p.motion_sources[p.face_id].root
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    previous = await phase(p)
    value = await new_face(p, face="B")
    await source_facts(p, value, root_client=root, revision=2)
    p.faces = [value]
    await advance(p, batch=batch)
    following = await phase(p)
    assert following.rack_face == "B" and following.id != previous.id
    assert following.inbound_client_request_id == previous.inbound_client_request_id
    assert following.round_started_at == previous.round_started_at
    assert following.round_causal_token == previous.round_causal_token
    assert following.closed_at is None and following.allocation_id is None
    assert following.source_feed_id != previous.source_feed_id


async def test_old_revision_end_cannot_satisfy_new_member_at_same_arrival(bound):
    p = bound
    old = p.motion_sources[p.face_id]
    ctx = await source_facts(p, p.faces[0], root_client=old.root, revision=2, reuse_arrival=True)
    correct = ctx.end_id
    ctx.end_id = old.end_id
    batch, _ = motion_application(p)
    with pytest.raises(ValueError, match="empty source end"):
        await advance(p, batch=batch)
    assert await phase(p) is None
    ctx.end_id = correct
    await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.source_feed_id == ctx.feed_id and saved.inbound_client_request_id == old.root


async def test_replay_cannot_rebind_phase_to_another_revision(bound):
    p = bound
    old = p.motion_sources[p.face_id]
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    previous = await phase(p)
    await source_facts(p, p.faces[0], root_client=old.root, revision=2, reuse_arrival=True)
    with pytest.raises(ValueError, match="binding identity drift"):
        await advance(p, batch=batch)
    assert (await phase(p)).source_feed_id == previous.source_feed_id


async def test_window_release_does_not_reset_round_or_source_owner(bound):
    from src.app.execution.models import TransportDecisionBinding

    p = bound
    ctx = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, ctx.root_id)).window_released_at = p.now
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.inbound_client_request_id == ctx.root
    assert await current_source(p, batch)


@pytest.mark.parametrize("same_rack", [True, False])
async def test_submitted_unacked_rack_action_is_an_object_dependency(bound, same_rack):
    p = bound
    await empty_candidates(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    saved = await phase(p)
    async with p.sessions.begin() as db:
        await rack_action(db, p, "OFFLINE-RACK" if same_rack else "INDEPENDENT", "B", root=object(), status="PENDING")
    assert await current_source(p, batch) is (not same_rack)
    assert (await phase(p)).closed_at == saved.closed_at
    assert await advance(p, batch=batch) == 0


@pytest.mark.parametrize("old_face", ["A", "B"])
async def test_old_round_unknowns_do_not_fence_new_round(qualified, old_face):
    from datetime import timedelta

    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    from src.app.execution.models import InboundEvidence

    p = qualified
    async with p.sessions.begin() as db:
        task, binding = await rack_action(db, p, "OFFLINE-RACK", "A", status="ACCEPTED")
        task.created_at = binding.created_at = p.now - timedelta(seconds=100)
        binding.window_released_at = p.now - timedelta(seconds=90)
        db.add(
            AutomaticPickingReturnAllocation(
                workline_id=p.line.id,
                arrival_evidence_id=p.face_id,
                rack_id="OFFLINE-RACK",
                rack_face=old_face,
                operation_id="old-round",
                return_ids=[],
                created_at=p.now - timedelta(seconds=90),
            )
        )
    await source_facts(p, p.faces[0])
    await empty_candidates(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    assert (await phase(p)).closed_at is not None
    assert await current_source(p, batch)
    async with p.sessions() as db:
        assert (await db.get(type(task), task.id)).status == "ACCEPTED"


async def test_source_binding_and_phase_roll_back_together(bound):
    from automatic_picking.application.return_motion_repository import ReturnMotionRepository

    p = bound
    batch, _ = motion_application(p)

    class Crash(ReturnMotionRepository):
        async def bind_source(self, db, current, intent, root):
            await super().bind_source(db, current, intent, root)
            raise RuntimeError("source binding crash")

    batch.return_batch_flow.motions = Crash()
    with pytest.raises(RuntimeError, match="source binding crash"):
        await advance(p, batch=batch)
    assert await phase(p) is None
    await advance(p, batch=motion_application(p)[0])
    assert (await phase(p)).inbound_client_request_id == p.motion_sources[p.face_id].root


async def test_concurrent_recovery_binds_one_phase(bound):
    import asyncio

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    p = bound
    batch, _ = motion_application(p)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=batch))
    async with p.sessions() as db:
        assert len(list((await db.scalars(select(AutomaticPickingReturnMotion))).all())) == 1


async def test_legacy_8e_exit_is_not_guessed_and_can_bind_without_reopening(bound):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from automatic_picking.application.work_repository import WorkRepository

    p = bound
    async with p.sessions.begin() as db:
        baseline = (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"]
        row = AutomaticPickingReturnMotion(
            workline_id=p.line.id,
            arrival_evidence_id=p.face_id,
            source_evidence_id=p.face_id,
            rack_id="OFFLINE-RACK",
            rack_face="A",
            closed_at=p.now,
        )
        db.add(row)
        await db.flush()
        row_id = row.id
    await advance(p, batch=application(p)[0])
    assert (await phase(p)).inbound_client_request_id is None
    async with p.sessions() as db:
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == baseline + 1
    await advance(p, batch=motion_application(p)[0])
    saved = await phase(p)
    assert saved.id == row_id and saved.closed_at == p.now
    assert saved.inbound_client_request_id == p.motion_sources[p.face_id].root
    assert saved.allocation_id is None
    async with p.sessions() as db:
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == baseline


async def test_bound_exit_recovers_after_cleaning_closed_feed_and_original_ingress(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportMember, TransportTask

    p = bound
    await empty_candidates(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    old = await phase(p)
    root = p.motion_sources[p.face_id]
    value = await new_face(p, face="B")
    ctx = await source_facts(p, value, root_client=root.root)
    p.faces = [value]
    await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.closed_at is not None and saved.inbound_client_request_id == root.root
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingReturnMotion, old.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id.in_((root.feed_id, ctx.feed_id))))
        await db.execute(
            delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id.in_((root.end_id, ctx.end_id)))
        )
        await db.delete(await db.get(InboundEvidence, p.face_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == root.transport_id))
        (await db.get(TransportDecisionBinding, root.root_id)).window_released_at = p.now
        await db.flush()
        await db.delete(await db.get(TransportDecisionBinding, root.root_id))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == root.transport_id))
    assert await advance(p, batch=application(p)[0]) == 0
    assert await current_source(p, batch)
    assert (await phase(p)).source_feed_id == ctx.feed_id


@pytest.mark.parametrize(
    "state", ["success", "unknown_position", "unpublished", "wrong_target", "wrong_source", "response_unpublished"]
)
async def test_ready_feed_end_requires_exact_final_handoff(bound, state):
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    from src.app.execution.models import InboundEvidence, WmsConfirmation
    from src.app.transport.models import TransportMember
    from src.core.uuid7 import new_uuid7
    from src.utils.canonical_json import canonical_json_digest

    p = bound
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
        response.normalized_payload = payload
        response.payload_digest = canonical_json_digest(payload)
        if state == "response_unpublished":
            response.published_at = None
        task, binding = await rack_action(db, p, "A000000001", "A", root=object(), kind="BIN_MOVE")
        binding.step = "AUTOMATIC_PICKING_FEED"
        binding.resource_fence_id = feed.operation_id
        binding.source_evidence_id = response.id
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
        )
        member.object_type = "BIN"
        member.arrival_face = None
        member.source_json = {"kind": "RACK_BIN_SLOT", "rack_id": "OFFLINE-RACK", "rack_face": "A", "slot_id": "S1"}
        member.target_json = {
            "kind": "HANDOFF_POSITION",
            "location_code": p.line.position_bindings["INLET"]["location_id"],
        }
        member.final_position_json = member.target_json
        if state == "wrong_source":
            member.source_json = {**member.source_json, "slot_id": "OTHER"}
        if state == "unknown_position":
            member.position_unknown = True
        if state == "wrong_target":
            member.final_position_json = {"kind": "RACK_POSITION", "location_code": "OTHER"}
        if state == "unpublished":
            task.published_outcome_version = 0
        payload = {
            "transport_task_id": task.transport_task_id,
            "client_request_id": task.client_request_id,
            "kind": "BIN_MOVE",
            "status": "SUCCEEDED",
            "outcome_version": 1,
        }
        end = InboundEvidence(
            kind="TRANSPORT_RESULT",
            source_identity="transport:" + new_uuid7(),
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            transport_task_id=task.transport_task_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now,
            decision_digest="d" * 64,
        )
        db.add(end)
        await db.flush()
        feed.offset = 1
        feed.transport_task_id = task.transport_task_id
        ctx.end_id = end.id
    await advance(p, batch=motion_application(p)[0])
    assert (await phase(p) is not None) is (state == "success")


async def test_migration_preserves_unbound_8e_and_refuses_loss_of_bound_context(bound, database):
    import subprocess

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from tests.support.postgresql_heavy import run_alembic

    p = bound
    async with p.sessions.begin() as db:
        db.add(
            AutomaticPickingReturnMotion(
                workline_id=p.line.id,
                arrival_evidence_id=p.face_id,
                source_evidence_id=p.face_id,
                rack_id="OFFLINE-RACK",
                rack_face="A",
                closed_at=p.now,
            )
        )
    url, _ = database
    run_alembic("downgrade", "b1692d740313", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert (await phase(p)).inbound_client_request_id is None
    await advance(p, batch=motion_application(p)[0])
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "b1692d740313", database_url=url)
    assert "cannot discard automatic motion evaluation source bindings" in error.value.stderr


async def test_current_direct_bin_action_without_allocation_row_still_blocks_rack(bound):
    from src.app.transport.models import TransportMember

    p = bound
    await empty_candidates(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        task, _ = await rack_action(db, p, "A000000002", "A", root=object(), kind="BIN_MOVE", status="PENDING")
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
        )
        member.object_type = "BIN"
        member.target_json = {"kind": "RACK_BIN_SLOT", "rack_id": "OFFLINE-RACK", "rack_face": "B", "slot_id": "S1"}
    assert not await current_source(p, batch)


async def test_recovery_waits_for_same_object_lock_and_observes_takeover(bound):
    import asyncio
    from types import SimpleNamespace as N

    from src.app.execution.repositories import transport_decision_binding_repository
    from src.core.uuid7 import new_uuid7
    from tests.integration.wms_adapter.outbound_picking.confirmation_support import seed_picking_owner

    p = bound
    async with p.sessions.begin() as db:
        peer_task, peer_line = await seed_picking_owner(
            db,
            operation_id=new_uuid7(),
            issued_operation_id=new_uuid7(),
            task_key="8F-PEER-" + new_uuid7(),
            now=p.now,
            status="EXECUTING",
        )
    peer = N(task=peer_task, line=peer_line, now=p.now)
    batch, _ = motion_application(p)
    async with p.sessions.begin() as db:
        await transport_decision_binding_repository.lock_object_authority(
            db, object_type="RACK", object_id="OFFLINE-RACK"
        )
        waiter = asyncio.create_task(advance(p, batch=batch))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        await rack_action(db, peer, "OFFLINE-RACK", "B", root=object(), status="PENDING")
    await waiter
    saved = await phase(p)
    assert saved.closed_at is None and saved.allocation_id is None
    assert not await current_source(p, batch)


async def test_authoritative_new_round_retires_unallocated_old_stage(bound):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    p.positions.clear()
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    previous = await phase(p)
    assert previous.closed_at is None and previous.allocation_id is None
    async with p.sessions.begin() as db:
        root = await db.get(TransportDecisionBinding, p.motion_sources[p.face_id].root_id)
        root.window_released_at = p.now
    value = await new_face(p, face="B")
    await source_facts(p, value)
    p.faces = [value]
    await empty_candidates(p)
    await advance(p, batch=batch)
    assert (await phase(p)).closed_at is not None
    async with p.sessions() as db:
        old = await db.get(AutomaticPickingReturnMotion, previous.id)
        assert old.archived_at is not None
        assert old.allocation_id is None


async def test_partial_round_binding_is_rejected_by_database(bound):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy.exc import IntegrityError

    p = bound
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            db.add(
                AutomaticPickingReturnMotion(
                    workline_id=p.line.id,
                    arrival_evidence_id=p.face_id,
                    source_evidence_id=p.face_id,
                    rack_id="OFFLINE-RACK",
                    rack_face="A",
                    inbound_client_request_id="root",
                    round_started_at=p.now,
                    source_feed_id=1,
                    service_end_evidence_id=p.face_id,
                )
            )
            await db.flush()


@pytest.mark.parametrize("root_before_old_request", [False, True])
async def test_complete_old_feed_end_cannot_bind_new_ingress(bound, root_before_old_request):
    from datetime import timedelta

    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    old = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, old.root_id)).window_released_at = p.now
    p.now += timedelta(seconds=60)
    value = await new_face(p)
    ctx = await source_facts(p, value)
    if root_before_old_request:
        async with p.sessions.begin() as db:
            (await db.get(TransportDecisionBinding, ctx.root_id)).created_at = p.now - timedelta(seconds=80)
    ctx.feed_id, ctx.end_id = old.feed_id, old.end_id
    p.faces = [value]
    batch, _ = motion_application(p)
    with pytest.raises(ValueError, match=r"source Feed.*round"):
        await advance(p, batch=batch)
    assert await phase(p) is None


@pytest.mark.parametrize("clean_intermediate", [False, True])
async def test_rotation_after_new_ingress_cannot_claim_old_round(bound, clean_intermediate):
    from datetime import timedelta

    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    old = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, old.root_id)).window_released_at = p.now
    p.now += timedelta(seconds=60)
    newer = await new_face(p)
    ctx = await source_facts(p, newer)
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, ctx.root_id)).window_released_at = p.now
    rotation = await new_face(p, face="B")
    await source_facts(p, rotation, root_client=old.root)
    p.faces = [rotation]
    if clean_intermediate:
        from src.app.execution.models import InboundEvidence
        from src.app.transport.models import TransportMember, TransportTask

        async with p.sessions.begin() as db:
            await db.delete(await db.get(InboundEvidence, newer[0]))
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == ctx.transport_id))
            await db.delete(await db.get(TransportDecisionBinding, ctx.root_id))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == ctx.transport_id))
    batch, _ = motion_application(p)
    with pytest.raises(ValueError, match="original round"):
        await advance(p, batch=batch)
    assert await phase(p) is None


@pytest.mark.parametrize("legacy", [False, True])
async def test_authoritative_other_workline_takeover_closes_unallocated_stage(bound, legacy):
    from datetime import timedelta
    from types import SimpleNamespace as N

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding
    from src.core.uuid7 import new_uuid7
    from tests.integration.wms_adapter.outbound_picking.confirmation_support import seed_picking_owner

    p = bound
    p.positions.clear()
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    previous = await phase(p)
    assert previous.closed_at is None and previous.allocation_id is None
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, p.motion_sources[p.face_id].root_id)).window_released_at = p.now
        task, line = await seed_picking_owner(
            db,
            operation_id=new_uuid7(),
            issued_operation_id=new_uuid7(),
            task_key="8F-TAKEOVER-" + new_uuid7(),
            now=p.now,
            status="EXECUTING",
        )
    from automatic_picking.application.work_repository import WorkRepository

    async with p.sessions.begin() as db:
        if legacy:
            saved = await db.get(AutomaticPickingReturnMotion, previous.id)
            for field in (
                "inbound_client_request_id",
                "round_started_at",
                "round_causal_token",
                "source_feed_id",
                "service_end_evidence_id",
            ):
                setattr(saved, field, None)
            await db.flush()
        before = (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"]
    peer = N(sessions=p.sessions, task=task, line=line, now=p.now + timedelta(seconds=60))
    value = await new_face(peer, face="B")
    await source_facts(peer, value)
    p.faces = []
    await advance(p, batch=batch)
    async with p.sessions() as db:
        old = await db.get(AutomaticPickingReturnMotion, previous.id)
        assert old.closed_at is not None
        assert (old.archived_at is not None) is legacy
        assert old.allocation_id is None
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == before - 1
    p.faces = [(p.face_id, "OFFLINE-RACK", "A")]
    assert await advance(p, batch=batch) == 0
    if legacy:
        async with p.sessions.begin() as db:
            await db.delete(await db.get(AutomaticPickingReturnMotion, previous.id))
        assert await advance(p, batch=batch) == 0
    from test_return_allocation_postgresql import allocation

    assert await allocation(p) is None


async def test_released_window_rotation_uses_direct_bound_source(bound):
    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    await empty_candidates(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    previous = await phase(p)
    root = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, root.root_id)).window_released_at = p.now
    value = await new_face(p, face="B")
    ctx = await source_facts(p, value, root_client=root.root)
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, ctx.binding_id)).source_evidence_id = previous.service_end_evidence_id
    p.faces = [value]
    await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.inbound_client_request_id == previous.inbound_client_request_id
    assert saved.round_causal_token == previous.round_causal_token
    assert saved.closed_at is not None


async def test_known_failed_ingress_does_not_override_successful_rotation(bound):
    from datetime import timedelta

    from test_return_allocation_postgresql import new_face

    from src.app.execution.models import InboundEvidence
    from src.app.transport.models import TransportMember, TransportTask
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    old = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == old.transport_id))
        task.status = "FAILED"
        member = await db.scalar(select(TransportMember).where(TransportMember.transport_task_id == old.transport_id))
        member.status = "FAILED"
        fact = await db.get(InboundEvidence, p.face_id)
        fact.normalized_payload = {**fact.normalized_payload, "status": "FAILED"}
        fact.payload_digest = canonical_json_digest(fact.normalized_payload)
    p.now += timedelta(seconds=60)
    value = await new_face(p, face="B")
    await source_facts(p, value, root_client=old.root)
    p.faces = [value]
    await empty_candidates(p)
    batch, _ = motion_application(p)
    await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.inbound_client_request_id == old.root and saved.closed_at is not None
    assert await current_source(p, batch)
