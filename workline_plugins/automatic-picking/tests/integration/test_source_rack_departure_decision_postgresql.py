"""8I 首次 departure 与 WAIT 当前游标；仅离线精确来源结束输入。"""

import pytest
from sqlalchemy import select
from test_return_allocation_postgresql import advance, arrived, database, observed, pending, qualified
from test_return_before_motion_postgresql import phase
from test_return_motion_binding_postgresql import bound
from test_source_rack_departure_postgresql import departure_application, departure_result

from src.app.execution.models import InboundEvidence, WmsConfirmation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
OPERATION = "outbound.rack.departure_decide@v1"


async def decision_application(p):
    from automatic_picking.application.source_rack_departure import MatchedSourceRackDeparture, SourceRackDepartureFlow

    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService

    batch, scan, previous = await departure_application(p)
    async with p.sessions.begin() as db:
        request = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.operation_id == p.departure_operation_id)
        )
        await db.delete(request)
        await db.flush()
        await db.delete(await db.get(InboundEvidence, p.departure_ready_id))
    current = await phase(p)
    p.departures = (MatchedSourceRackDeparture(current.id),)
    scheduler = RackDepartureScheduler(WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService()))
    flow = SourceRackDepartureFlow(
        previous.returns, previous.creator, previous.reader, scheduler=scheduler, clock=lambda: p.now
    )
    batch.rack_departure_flow = scan.rack_departure_flow = flow
    return batch, scan, flow


async def requests(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == OPERATION).order_by(WmsConfirmation.id)
                )
            ).all()
        )


async def test_first_departure_request_and_current_reference_are_atomic_and_unique(bound):
    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id is not None
    assert current.departure_transport_task_id is None
    await advance(p, batch=batch)
    saved = await requests(p)
    assert len(saved) == 1
    request = saved[0]
    assert request.operation_id == current.departure_operation_id
    assert request.workline_id == p.line.id and request.picking_task_id is None
    assert request.request_payload["data"] == {
        "task_id": None,
        "rack_id": "OFFLINE-RACK",
        "current_location": {"type": "RACK_POSITION", "location_code": "OFFLINE-FIVE"},
        "current_face": "A",
    }
    assert (await phase(p)).departure_operation_id == current.departure_operation_id


async def decided(p, result="WAIT", *, operation_id=None, publish=True):
    from src.utils.canonical_json import canonical_json_digest

    current = await phase(p)
    operation_id = operation_id or current.departure_operation_id
    data = {"result": result}
    if result == "WAIT":
        data["retry_after_ms"] = 1000
    else:
        data["rack_destination"] = {"type": "RACK_POSITION", "location_code": "OFFLINE-OUT"}
    payload = {"operation_id": operation_id, "timestamp": 1, "code": "DECIDED", "data": data}
    async with p.sessions.begin() as db:
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
        evidence = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=OPERATION + ":" + operation_id,
            operation=OPERATION,
            operation_id=operation_id,
            workline_id=p.line.id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            published_at=p.now if publish else None,
            received_at=p.now,
            decision_digest="c" * 64,
        )
        db.add(evidence)
        await db.flush()
        request.status = "COMPLETED"
        request.response_evidence_id = evidence.id
        request.response_result = result
        request.completed_at = p.now
        return evidence.id


async def test_wait_due_renews_once_without_reader_then_ready_rejoins_ctu03(bound):
    from datetime import timedelta

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = bound
    batch, scan, flow = await decision_application(p)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_operation_id
    await decided(p)
    await advance(p, batch=batch)
    deadline = (await phase(p)).departure_retry_at
    assert deadline == p.now + timedelta(milliseconds=1000)
    flow.reader = batch.return_batch_flow.motion_reader = None
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    p.now += timedelta(milliseconds=999)
    await advance(p, batch=batch)
    assert (await phase(p)).departure_retry_at == deadline
    assert len(await requests(p)) == 1
    p.now += timedelta(milliseconds=1)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id != original and current.departure_retry_at is None
    assert len(await requests(p)) == 2
    await advance(p, batch=batch)
    assert len(await requests(p)) == 2
    await decided(p, "READY")
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None
    await departure_result(p, batch, scan)
    await advance(p, batch=batch)
    assert (await phase(p)).archived_at is not None


@pytest.mark.parametrize("waiting", [False, True])
async def test_concurrent_scans_create_one_first_or_due_request(bound, waiting):
    import asyncio
    from datetime import timedelta

    p = bound
    batch, _, _ = await decision_application(p)
    if waiting:
        await advance(p, batch=batch)
        await decided(p)
        await advance(p, batch=batch)
        p.now += timedelta(seconds=1)
    await asyncio.gather(*(advance(p, batch=batch) for _ in range(6)))
    assert len(await requests(p)) == (2 if waiting else 1)
    assert (await phase(p)).departure_transport_task_id is None


@pytest.mark.parametrize("waiting", [False, True])
async def test_request_creation_and_cursor_swap_rollback_together(bound, waiting):
    from datetime import timedelta

    p = bound
    batch, _, flow = await decision_application(p)
    if waiting:
        await advance(p, batch=batch)
        await decided(p)
        await advance(p, batch=batch)
        p.now += timedelta(seconds=1)
    before = await phase(p)
    scheduler = flow.scheduler

    class Crash:
        async def create_in_session(self, db, intent, **kwargs):
            await scheduler.create_in_session(db, intent, **kwargs)
            raise RuntimeError("offline crash before commit")

    flow.scheduler = Crash()
    with pytest.raises(RuntimeError, match="offline crash"):
        await advance(p, batch=batch)
    after = await phase(p)
    assert after.departure_operation_id == before.departure_operation_id
    assert after.departure_retry_at == before.departure_retry_at
    assert len(await requests(p)) == (1 if waiting else 0)
    flow.scheduler = scheduler
    await advance(p, batch=batch)
    assert len(await requests(p)) == (2 if waiting else 1)


async def test_restarted_owner_freezes_wait_deadline_from_original_evidence(bound):
    from datetime import timedelta

    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow

    p = bound
    batch, _, flow = await decision_application(p)
    await advance(p, batch=batch)
    await decided(p)
    received_at = p.now
    p.now += timedelta(milliseconds=700)
    batch.rack_departure_flow = SourceRackDepartureFlow(
        flow.returns, flow.creator, None, scheduler=flow.scheduler, clock=lambda: p.now
    )
    batch.return_batch_flow.motion_reader = None
    await advance(p, batch=batch)
    assert (await phase(p)).departure_retry_at == received_at + timedelta(seconds=1)
    assert len(await requests(p)) == 1
    p.now += timedelta(milliseconds=300)
    await advance(p, batch=batch)
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("result_state", ["pending", "unpublished", "reconciling"])
async def test_unknown_or_unpublished_result_never_becomes_wait_or_new_identity(bound, result_state):
    from datetime import timedelta

    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_operation_id
    if result_state == "unpublished":
        await decided(p, publish=False)
    elif result_state == "reconciling":
        async with p.sessions.begin() as db:
            (
                await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == original))
            ).status = "RECONCILING"
    p.now += timedelta(days=1)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id == original and current.departure_retry_at is None
    assert current.departure_transport_task_id is None and len(await requests(p)) == 1


@pytest.mark.parametrize("fault", ["request_owner", "response_owner", "response_digest", "response_identity"])
async def test_current_response_identity_drift_cannot_renew_or_create_transport(bound, fault):
    from datetime import timedelta

    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_operation_id
    evidence_id = await decided(p)
    async with p.sessions.begin() as db:
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == original))
        evidence = await db.get(InboundEvidence, evidence_id)
        if fault == "request_owner":
            request.workline_id = None
            request.picking_task_id = p.task.id
        elif fault == "response_owner":
            evidence.picking_task_id = p.task.id
        elif fault == "response_digest":
            evidence.payload_digest = "0" * 64
        else:
            evidence.operation_id = "different-operation"
    p.now += timedelta(seconds=1)
    with pytest.raises(ValueError):
        await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id == original and current.departure_retry_at is None
    assert current.departure_transport_task_id is None and len(await requests(p)) == 1


async def test_old_completed_results_and_cleanup_cannot_drive_new_request(bound):
    from datetime import timedelta

    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow
    from sqlalchemy import delete

    p = bound
    batch, _, flow = await decision_application(p)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_operation_id
    old_wait = await decided(p)
    await advance(p, batch=batch)
    p.now += timedelta(seconds=1)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id != original
    # 原 WAIT 重放仍是同一已保存事实，扫描只定位当前游标，不按最新/历史决定推进。
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is None
    source = p.motion_sources[p.face_id]
    batch.return_batch_flow.motion_reader = None
    async with p.sessions.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == original))
        await db.delete(await db.get(InboundEvidence, old_wait))
        await db.delete(await db.get(AutomaticPickingFeed, source.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == source.end_id))
    batch.rack_departure_flow = SourceRackDepartureFlow(
        flow.returns, flow.creator, None, scheduler=flow.scheduler, clock=lambda: p.now
    )
    await decided(p, "READY")
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None


async def test_future_revision_cannot_extend_frozen_departure_or_start_feed(bound):
    from datetime import timedelta

    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    await decided(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=99,
            rack_id="OFFLINE-RACK",
            rack_face="A",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        assert not await batch.feeds.source_ready(db, p.line, member)
    p.now += timedelta(seconds=1)
    await advance(p, batch=batch)
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("same_rack", [False, True])
async def test_only_direct_current_rack_unknown_blocks_due_renewal(bound, same_rack):
    from datetime import timedelta

    from motion_binding_support import rack_action

    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    await decided(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        await rack_action(
            db, p, "OFFLINE-RACK" if same_rack else "INDEPENDENT-RACK", "B", root=object(), status="PENDING"
        )
    p.now += timedelta(seconds=1)
    await advance(p, batch=batch)
    assert len(await requests(p)) == (1 if same_rack else 2)
    assert (await phase(p)).archived_at is None


async def given_takeover(p, *, other_line=False):
    from motion_binding_support import rack_action

    from src.app.execution.models import PositionProjection
    from src.utils.canonical_json import canonical_json_digest

    async with p.sessions.begin() as db:
        line_id = p.line.id
        if other_line:
            line = type(p.line)(
                line_code="OTHER-OFFLINE",
                line_name="Other offline",
                line_type=p.line.line_type,
                position_bindings=p.line.position_bindings,
            )
            db.add(line)
            await db.flush()
            line_id = line.id
        task, binding = await rack_action(db, p, "OFFLINE-RACK", "B", root=object(), line_id=line_id)
        binding.picking_task_id = None
        payload = {
            "transport_task_id": task.transport_task_id,
            "client_request_id": task.client_request_id,
            "outcome_version": 1,
            "status": "SUCCEEDED",
        }
        db.add(
            InboundEvidence(
                kind="TRANSPORT_RESULT",
                source_identity="offline-current:" + task.transport_task_id,
                received_at=p.now,
                transport_task_id=task.transport_task_id,
                workline_id=line_id,
                normalized_payload=payload,
                payload_digest=canonical_json_digest(payload),
                apply_status="APPLIED",
                published_at=p.now,
                decision_digest="c" * 64,
            )
        )
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
            )
        )
        position.workline_id, position.position_unknown, position.arrival_face = line_id, False, "B"
        position.position_json = {"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}
        position.source_transport_task_id, position.source_causal_token = task.transport_task_id, binding.causal_token


@pytest.mark.parametrize("other_line", [False, True])
async def test_positive_new_round_takeover_retires_old_wait_without_reopening_it(bound, other_line):
    from datetime import timedelta

    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    await decided(p)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_operation_id
    await given_takeover(p, other_line=other_line)
    p.now += timedelta(seconds=1)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.archived_at is not None and current.departure_operation_id == original
    assert current.departure_transport_task_id is None and len(await requests(p)) == 1
    await advance(p, batch=batch)
    assert len(await requests(p)) == 1


async def test_unfinished_service_does_not_create_first_decision(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed

    p = bound
    batch, _, _ = await decision_application(p)
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingFeed, p.motion_sources[p.face_id].feed_id)).phase = "FEEDING"
    batch.return_batch_flow.motion_reader = None
    await advance(p, batch=batch)
    assert not await requests(p)
    assert (await phase(p)).departure_operation_id is None


async def test_request_only_stage_remains_current_owner_and_blocks_rotation(bound):
    from automatic_picking.application.work_repository import WorkRepository
    from test_source_rack_motion_postgresql import rotation_application

    from src.app.transport.models import TransportTask

    p = bound
    batch, _, _ = await decision_application(p)
    async with p.sessions() as db:
        before = await WorkRepository().get_unfinished_workload_summary(db, p.line.id)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_operation_id
    async with p.sessions() as db:
        after = await WorkRepository().get_unfinished_workload_summary(db, p.line.id)
    assert after["count"] == before["count"] + 1
    rotation_batch, _, _ = await rotation_application(p)
    await advance(p, batch=rotation_batch)
    current = await phase(p)
    assert current.departure_operation_id == original and current.archived_at is None
    assert current.rotation_transport_task_id is None
    async with p.sessions() as db:
        assert not list((await db.scalars(select(TransportTask).where(TransportTask.kind == "RACK_ROTATE"))).all())


async def test_schema_preserves_8h_and_refuses_to_drop_request_only_progress(bound, database):
    import subprocess

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from tests.support.postgresql_heavy import run_alembic

    p = bound
    batch, _, _ = await decision_application(p)
    before = await phase(p)
    url, _ = database
    run_alembic("downgrade", "43401479b2cc", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    after = await phase(p)
    assert (after.id, after.inbound_client_request_id, after.service_end_evidence_id) == (
        before.id,
        before.inbound_client_request_id,
        before.service_end_evidence_id,
    )
    assert after.departure_retry_at is None

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

    async with p.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []
    await advance(p, batch=batch)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "43401479b2cc", database_url=url)
    assert "cannot discard automatic source departure decision" in error.value.stderr


async def test_request_only_schema_rejects_orphan_deadline_and_result(bound):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy.exc import IntegrityError

    p = bound
    batch, _, _ = await decision_application(p)
    current = await phase(p)
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            (await db.get(AutomaticPickingReturnMotion, current.id)).departure_retry_at = p.now
            await db.flush()
    await advance(p, batch=batch)
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            (
                await db.get(AutomaticPickingReturnMotion, current.id)
            ).departure_result_evidence_id = current.service_end_evidence_id
            await db.flush()


async def test_fact_processor_freezes_wait_and_publishes_in_one_transaction(bound):
    from datetime import timedelta

    from test_return_allocation_postgresql import processor

    p = bound
    batch, scan, _ = await decision_application(p)
    await advance(p, batch=batch)
    evidence_id = await decided(p, publish=False)
    await processor(p, scan=scan).process_batch()
    async with p.sessions() as db:
        evidence = await db.get(InboundEvidence, evidence_id)
        assert evidence.published_at is not None
    assert (await phase(p)).departure_retry_at == p.now + timedelta(seconds=1)


async def test_real_confirmation_storage_and_fact_processor_rejoin_ready_without_http(bound):
    from datetime import timedelta

    from test_return_allocation_postgresql import processor

    from src.app.execution.services import WmsConfirmationService
    from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService

    p = bound
    batch, scan, _ = await decision_application(p)
    await advance(p, batch=batch)

    class OfflineAdapter:
        result = "WAIT"

        async def dispatch(self, *, operation, operation_id, request_payload, request_digest, observation=None):
            data = {"result": self.result}
            if self.result == "WAIT":
                data["retry_after_ms"] = 1000
            else:
                data["rack_destination"] = {"type": "RACK_POSITION", "location_code": "OFFLINE-OUT"}
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE,
                normalized_response={"operation_id": operation_id, "timestamp": 1, "code": "DECIDED", "data": data},
                response_result=self.result,
            )

    adapter = OfflineAdapter()
    service = WmsConfirmationService(
        session_factory=p.sessions, adapter=adapter, workline_owner=RackDepartureOwnerService()
    )
    assert await service.dispatch_batch(limit=1, now=p.now) == 1
    first = (await requests(p))[0]
    async with p.sessions() as db:
        evidence = await db.get(InboundEvidence, first.response_evidence_id)
        assert evidence.apply_status == "APPLIED" and evidence.published_at is None
    await processor(p, scan=scan).process_batch()
    assert (await phase(p)).departure_retry_at == p.now + timedelta(seconds=1)
    p.now += timedelta(seconds=1)
    await advance(p, batch=batch)
    adapter.result = "READY"
    assert await service.dispatch_batch(limit=1, now=p.now) == 1
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is None
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None


async def test_wait_application_and_publication_rollback_then_recover_together(bound):
    from datetime import timedelta

    from test_return_allocation_postgresql import processor

    from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository

    p = bound
    batch, scan, _ = await decision_application(p)
    await advance(p, batch=batch)
    evidence_id = await decided(p, publish=False)
    received_at = p.now

    class CrashPublication(InboundEvidenceRepository):
        failed = False

        async def flush(self, db):
            if not self.failed and any(
                isinstance(row, InboundEvidence) and row.id == evidence_id and row.published_at is not None
                for row in db.identity_map.values()
            ):
                self.failed = True
                raise RuntimeError("offline publication crash")
            await super().flush(db)

    repository = CrashPublication()
    await processor(p, scan=scan, evidences=repository).process_batch()
    assert repository.failed
    assert (await phase(p)).departure_retry_at is None
    async with p.sessions() as db:
        evidence = await db.get(InboundEvidence, evidence_id)
        assert evidence.published_at is None
    p.now += timedelta(seconds=3)
    await processor(p, scan=scan).process_batch()
    assert (await phase(p)).departure_retry_at == received_at + timedelta(seconds=1)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, evidence_id)).published_at is not None


@pytest.mark.parametrize("pending_same_rack", [False, True])
async def test_rotated_current_face_and_same_round_unknown_are_preserved(bound, pending_same_rack):
    from datetime import timedelta

    from motion_binding_support import rack_action
    from test_source_rack_motion_postgresql import rotation_application, rotation_result

    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
    from src.utils.timezone import timezone

    p = bound
    rotation_batch, rotation_scan, _ = await rotation_application(p)
    await advance(p, batch=rotation_batch)
    await rotation_result(p, rotation_batch, rotation_scan)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "CANCELLED"
        (await db.get(PickingTaskBinSourceRack, p.next_member_id)).cancelled_evidence_id = p.task.issued_evidence_id
    batch, _, _ = await decision_application(p)
    p.now = timezone.now_for_db()
    await advance(p, batch=batch)
    saved = await requests(p)
    assert len(saved) == 1 and saved[0].request_payload["data"]["current_face"] == "B"
    await decided(p)
    await advance(p, batch=batch)
    if pending_same_rack:
        async with p.sessions.begin() as db:
            await rack_action(db, p, "OFFLINE-RACK", "A", root=object(), status="PENDING")
    p.now += timedelta(seconds=1)
    await advance(p, batch=batch)
    assert len(await requests(p)) == (1 if pending_same_rack else 2)
    assert (await phase(p)).archived_at is None


async def test_window_release_does_not_reset_round_or_wait_owner(bound):
    from datetime import timedelta

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    batch, _, _ = await decision_application(p)
    before = await phase(p)
    async with p.sessions.begin() as db:
        (await db.get(TransportDecisionBinding, p.motion_sources[p.face_id].root_id)).window_released_at = p.now
    await advance(p, batch=batch)
    await decided(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.inbound_client_request_id == before.inbound_client_request_id
    assert current.round_started_at == before.round_started_at
    assert current.departure_retry_at == p.now + timedelta(seconds=1)


async def test_unpublished_departure_keeps_independent_feed_progress_committable(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from motion_binding_support import source_facts
    from test_return_allocation_postgresql import new_face

    p = bound
    batch, _, _ = await decision_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    original = current.departure_operation_id
    await decided(p, publish=False)
    independent = await new_face(p, rack="INDEPENDENT-RACK")
    source = await source_facts(p, independent)
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingFeed, source.feed_id)).phase = "REQUESTED"
    # 独立 Feed 已有最终 RACK_FACE_DONE，正常 batch 应提交其推进，当前 departure 仅等待发布。
    await advance(p, batch=batch)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingFeed, source.feed_id)).phase == "FEED_COMPLETE"
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    async with p.sessions() as db:
        current = await db.get(AutomaticPickingReturnMotion, current.id)
        assert current.departure_operation_id == original and current.departure_transport_task_id is None
