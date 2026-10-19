"""普通来源架 CTU03：显式 READY 的离线事务与恢复。"""

import pytest
from test_return_allocation_postgresql import advance, arrived, database, observed, pending, qualified
from test_return_before_motion_postgresql import motion_application, phase
from test_return_motion_binding_postgresql import bound, empty_candidates

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_batch_recovers_departure_before_executing_task_lookup(bound):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = bound
    await empty_candidates(p)
    batch, _ = motion_application(p)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    calls = []

    class Owner:
        async def advance_in_session(self, db, line):
            calls.append(line.id)
            return 0

    batch.rack_departure_flow = Owner()
    await advance(p, batch=batch)
    assert calls == [p.line.id]


async def given_ready(p, face="A"):
    from automatic_picking.application.source_rack_departure import MatchedSourceRackDeparture
    from sqlalchemy import select
    from wes_plugin_sdk import TransportRackPosition, wms_operations

    from src.app.execution.models import InboundEvidence, WmsConfirmation
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
    from src.core.uuid7 import new_uuid7
    from src.utils.canonical_json import canonical_json_digest
    from src.utils.timezone import timezone

    current = await phase(p)
    p.now = timezone.now_for_db()
    p.departure_operation_id = new_uuid7()
    intent = wms_operations.outbound_rack_departure_decide(
        operation_id=p.departure_operation_id,
        task_id=None,
        rack_id="OFFLINE-RACK",
        current_location=TransportRackPosition("OFFLINE-FIVE"),
        current_face=face,
    )
    scheduler = RackDepartureScheduler(WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService()))
    async with p.sessions.begin() as db:
        await scheduler.create_in_session(db, intent, workline_id=p.line.id, created_at=p.now)
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == intent.operation_id))
        payload = {
            "operation_id": intent.operation_id,
            "timestamp": 1,
            "code": "DECIDED",
            "data": {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-OUT"}},
        }
        fact = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=request.operation + ":" + intent.operation_id,
            operation=request.operation,
            operation_id=intent.operation_id,
            workline_id=p.line.id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            published_at=p.now,
            received_at=p.now,
            decision_digest="c" * 64,
        )
        db.add(fact)
        await db.flush()
        request.status, request.response_evidence_id, request.response_result, request.completed_at = (
            "COMPLETED",
            fact.id,
            "READY",
            p.now,
        )
        p.departure_ready_id = fact.id
    p.departures = (MatchedSourceRackDeparture(current.id, intent.operation_id),)


async def departure_application(p):
    from datetime import timedelta
    from types import SimpleNamespace as N

    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow
    from sqlalchemy import select

    from src.app.execution.models import PositionProjection
    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService

    await empty_candidates(p)
    batch, scan = motion_application(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        projection = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
            )
        )
        projection.source_effect_phase = "FINAL_RESULT"
    await given_ready(p)

    class Reader:
        async def read_in_session(self, db, *, workline_id):
            return p.departures

    class Events:
        async def publish_to(self, *args, **kwargs):
            return False

    window = RackInboundWindowService()

    class Diagnostics:
        async def start(self, **kwargs):
            return None

        async def finish(self, observation):
            return None

    service = TransportService(
        p.sessions,
        TransportRepository(),
        N(),
        diagnostics=Diagnostics(),
        result_timeout=timedelta(seconds=420),
        event_publisher=Events(),
        progress_hook=window.on_transport_progress,
    )
    creator = ReliableRackTransportCreator(service, inbound_window=window)
    flow = SourceRackDepartureFlow(batch.return_batch_flow, creator, Reader(), clock=lambda: p.now)
    batch.rack_departure_flow = flow
    scan.rack_departure_flow = flow
    p.departure_service = service
    return batch, scan, flow


async def test_creates_unique_ctu03_and_associates_original_ready(bound):
    from sqlalchemy import select

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportTask

    p = bound
    batch, _, _ = await departure_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    assert getattr(current, "departure_transport_task_id", None) is not None
    await advance(p, batch=batch)
    async with p.sessions() as db:
        tasks = list(
            (
                await db.scalars(
                    select(TransportTask).where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU03")
                )
            ).all()
        )
        assert len(tasks) == 1
        task = tasks[0]
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == task.client_request_id)
        )
        assert binding.picking_task_id is None
        assert binding.source_evidence_id == p.departure_ready_id
        assert binding.correlation_id == "departure:" + p.departure_operation_id
        assert task.request_json["target"] == {"kind": "RACK_POSITION", "location_code": "OFFLINE-OUT"}


async def departure_result(p, batch, scan, *, unknown=False, publish=True):
    from types import SimpleNamespace as N

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository
    from test_return_allocation_postgresql import processor

    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
    from src.core.uuid7 import new_uuid7
    from src.utils.timezone import timezone

    current = await phase(p)
    identity = new_uuid7()
    data = {
        "transport_task_id": current.departure_transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "OFFLINE-RACK",
        "status": "FAILED" if unknown else "SUCCEEDED",
        "position_unknown": unknown,
    }
    if unknown:
        data["failure_code"] = "OFFLINE_UNKNOWN"
    else:
        data.update(final_position={"kind": "RACK_POSITION", "location_code": "OFFLINE-OUT"}, arrival_face="A")
    message = {"operation": "transport.task.resulted@v1", "operation_id": identity, "timestamp": 1, "data": data}
    service = p.departure_service
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
    if publish:
        plugin = build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository())
        publisher = InstalledPluginTransportOutcomePublisher(
            p.sessions,
            (plugin,),
            queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
        )
        assert await service.publish_pending_outcomes(100, publisher) == 1
        await processor(p, scan=scan).process_batch()
    p.now = timezone.now_for_db()


async def test_callback_before_ack_releases_original_window_and_closes_only_published_result(bound):
    from sqlalchemy import select

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    batch, scan, _ = await departure_application(p)
    await advance(p, batch=batch)
    await departure_result(p, batch, scan)
    observed = await phase(p)
    assert observed.departure_result_evidence_id is not None
    async with p.sessions() as db:
        root = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == observed.inbound_client_request_id
            )
        )
        assert root.window_released_at is not None
    await advance(p, batch=batch)
    assert (await phase(p)).archived_at is not None


async def test_terminal_parent_rotation_hands_current_round_to_departure_owner(bound):
    from test_source_rack_motion_postgresql import rotation_application, rotation_result

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = bound
    batch, scan, _ = await rotation_application(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await rotation_result(p, batch, scan)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.rotation_result_evidence_id is not None
    assert current.archived_at is None


async def test_later_revision_cannot_start_feed_after_departure_is_frozen(bound):
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = bound
    batch, _, _ = await departure_application(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            plan_revision=30,
            rack_id="OFFLINE-RACK",
            rack_face="A",
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        assert not await batch.feeds.source_ready(db, p.line, member)


async def test_concurrent_creation_and_result_close_keep_one_transport(bound):
    import asyncio

    from sqlalchemy import func, select

    from src.app.transport.models import TransportTask

    p = bound
    batch, scan, _ = await departure_application(p)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=batch))
    before = await phase(p)
    await departure_result(p, batch, scan)
    await asyncio.gather(advance(p, batch=batch), advance(p, batch=batch))
    async with p.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TransportTask)
                .where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU03")
            )
            == 1
        )
    assert (await phase(p)).departure_transport_task_id == before.departure_transport_task_id
    assert (await phase(p)).archived_at is not None


@pytest.mark.parametrize("point", ["bind", "finish"])
async def test_transaction_crash_restarts_from_current_references(bound, point):
    from sqlalchemy import select

    from src.app.execution.models import TransportDecisionBinding

    p = bound
    batch, scan, flow = await departure_application(p)
    method = "bind" if point == "bind" else "finished"
    if point == "finish":
        await advance(p, batch=batch)
        await departure_result(p, batch, scan)
    original = getattr(flow.repository, method)

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline transaction crash")

    setattr(flow.repository, method, crash)
    with pytest.raises(RuntimeError, match="offline transaction crash"):
        await advance(p, batch=batch)
    current = await phase(p)
    assert current.archived_at is None
    if point == "bind":
        assert current.departure_transport_task_id is None
        async with p.sessions() as db:
            root = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == current.inbound_client_request_id
                )
            )
            assert root.window_departure_client_request_id is None
    flow = type(flow)(flow.returns, flow.creator, flow.reader if point == "bind" else None, clock=lambda: p.now)
    batch.rack_departure_flow = scan.rack_departure_flow = flow
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None
    if point == "finish":
        assert (await phase(p)).archived_at is not None


async def test_simulated_accepted_releases_window_without_closing_physical_duty(bound):
    from sqlalchemy import select

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.app.transport.models import TransportTask

    p = bound
    batch, _, _ = await departure_application(p)
    await advance(p, batch=batch)
    calls = []

    class Provider:
        async def submit(self, **kwargs):
            calls.append(kwargs)
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.departure_service.provider = Provider()
    assert await p.departure_service.submit_pending_tasks(1) == 1
    current = await phase(p)
    async with p.sessions() as db:
        root = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == current.inbound_client_request_id
            )
        )
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current.departure_transport_task_id)
        )
        assert task.status == "ACCEPTED" and root.window_released_at is not None
    await advance(p, batch=batch)
    assert len(calls) == 1
    assert (await phase(p)).archived_at is None
    assert (await phase(p)).departure_result_evidence_id is None


@pytest.mark.parametrize("state", ["unknown", "unpublished"])
async def test_uncertain_or_unpublished_result_retains_original_duty(bound, state):
    p = bound
    batch, scan, flow = await departure_application(p)
    await advance(p, batch=batch)
    current = await phase(p)
    await departure_result(p, batch, scan, unknown=state == "unknown", publish=state != "unpublished")
    flow.reader = None
    await advance(p, batch=batch)
    assert (await phase(p)).archived_at is None
    assert (await phase(p)).departure_transport_task_id == current.departure_transport_task_id


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_face",
        "wrong_location",
        "payload_drift",
        "wrong_owner",
        "unpublished",
        "stage_open",
        "unfinished_feed",
        "same_rack_pending",
        "other_rack_pending",
    ],
)
async def test_creation_requires_current_source_and_exact_published_ready(bound, fault):
    from datetime import timedelta

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from motion_binding_support import rack_action
    from sqlalchemy import select

    from src.app.execution.models import InboundEvidence, WmsConfirmation
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    batch, _, _ = await departure_application(p)
    current = await phase(p)
    async with p.sessions.begin() as db:
        request = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.operation_id == p.departure_operation_id)
        )
        if fault in {"wrong_face", "wrong_location", "payload_drift"}:
            payload = {**request.request_payload, "data": {**request.request_payload["data"]}}
            if fault == "wrong_face":
                payload["data"]["current_face"] = "B"
            else:
                payload["data"]["current_location"] = {"type": "RACK_POSITION", "location_code": "OTHER"}
            request.request_payload = payload
            if fault != "payload_drift":
                request.request_digest = canonical_json_digest(payload)
        elif fault == "wrong_owner":
            request.picking_task_id = p.task.id
            request.workline_id = None
        elif fault == "unpublished":
            (await db.get(InboundEvidence, p.departure_ready_id)).published_at = None
        elif fault == "stage_open":
            (await db.get(AutomaticPickingReturnMotion, current.id)).closed_at = None
        elif fault == "unfinished_feed":
            from automatic_picking.application.feed_model import AutomaticPickingFeed

            (await db.get(AutomaticPickingFeed, p.motion_sources[p.face_id].feed_id)).phase = "FEEDING"
        else:
            await rack_action(
                db,
                p,
                "OFFLINE-RACK" if fault == "same_rack_pending" else "INDEPENDENT-RACK",
                "A",
                root=object(),
                status="PENDING",
            )
    batch.return_batch_flow = None
    if fault in {"wrong_face", "wrong_location", "payload_drift", "wrong_owner", "unpublished"}:
        with pytest.raises(ValueError):
            await advance(p, batch=batch)
    else:
        await advance(p, batch=batch)
    assert ((await phase(p)).departure_transport_task_id is not None) == (fault == "other_rack_pending")


async def test_later_revision_after_ready_does_not_reopen_departure_evaluation(bound):
    from datetime import timedelta

    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = bound
    batch, _, _ = await departure_application(p)
    async with p.sessions.begin() as db:
        db.add(
            PickingTaskBinSourceRack(
                picking_task_id=p.task.id,
                plan_revision=40,
                rack_id="OFFLINE-RACK",
                rack_face="B",
                source_evidence_id=p.task.issued_evidence_id,
                created_at=p.now + timedelta(seconds=1),
            )
        )
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None


async def test_released_window_is_not_recreated_and_old_result_does_not_reopen_phase(bound):
    from sqlalchemy import select

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding

    p = bound
    batch, scan, flow = await departure_application(p)
    async with p.sessions.begin() as db:
        root = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == (await phase(p)).inbound_client_request_id
            )
        )
        root.window_released_at = p.now
    await advance(p, batch=batch)
    await departure_result(p, batch, scan)
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, current.departure_result_evidence_id)
        assert (
            await flow.apply_transport_result_in_session(db, line=p.line, evidence=fact)
            == "SOURCE_DEPARTURE_OLD_OUTCOME"
        )
        root = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == current.inbound_client_request_id
            )
        )
        assert root.window_released_at == p.now or root.window_departure_client_request_id is None
    assert await advance(p, batch=batch) == 0


async def test_no_executing_task_still_closes_original_departure(bound):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = bound
    batch, scan, flow = await departure_application(p)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await departure_result(p, batch, scan)
    flow.reader = None
    await advance(p, batch=batch)
    assert (await phase(p)).archived_at is not None


async def accept_departure(p):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.departure_service.provider = Provider()
    assert await p.departure_service.submit_pending_tasks(1) == 1


async def test_restarts_after_cleaning_closed_source_and_original_ingress(bound):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import delete

    from src.app.execution.models import TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportMember, TransportTask

    p = bound
    batch, scan, flow = await departure_application(p)
    await advance(p, batch=batch)
    before = await phase(p)
    await accept_departure(p)
    source = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingFeed, source.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == source.end_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == source.transport_id))
        await db.delete(await db.get(TransportDecisionBinding, source.root_id))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == source.transport_id))
    batch.return_batch_flow.motion_reader = None
    flow = type(flow)(flow.returns, flow.creator, None, clock=lambda: p.now)
    batch.rack_departure_flow = scan.rack_departure_flow = flow
    await departure_result(p, batch, scan)
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id == before.departure_transport_task_id
    assert (await phase(p)).archived_at is not None


async def test_archived_result_replay_does_not_require_closed_ready_confirmation(bound):
    from sqlalchemy import delete

    from src.app.execution.models import InboundEvidence, WmsConfirmation

    p = bound
    batch, scan, flow = await departure_application(p)
    await advance(p, batch=batch)
    await departure_result(p, batch, scan)
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == p.departure_operation_id))
    async with p.sessions.begin() as db:
        evidence = await db.get(InboundEvidence, current.departure_result_evidence_id)
        assert (
            await flow.apply_transport_result_in_session(db, line=p.line, evidence=evidence)
            == "SOURCE_DEPARTURE_OLD_OUTCOME"
        )


async def test_rotated_current_face_and_cancelled_next_member_depart_after_parent_end(bound):
    from dataclasses import replace

    from sqlalchemy import select
    from test_source_rack_motion_postgresql import rotation_application, rotation_result

    from src.app.execution.models import WmsConfirmation
    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    batch, _, _ = await departure_application(p)
    rotating, rotating_scan, _ = await rotation_application(p)
    await advance(p, batch=rotating)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        (await db.get(PickingTaskBinSourceRack, p.next_member_id)).cancelled_evidence_id = p.task.issued_evidence_id
    await rotation_result(p, rotating, rotating_scan)
    await advance(p, batch=rotating)
    await given_ready(p, face="B")
    # Existing current stage/round was retained; departure's reader supplies this exact READY.
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None


async def test_migration_and_schema_preserve_prior_round_and_guard_downgrade(bound, database):
    import subprocess

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    from tests.support.postgresql_heavy import run_alembic

    p = bound
    batch, _, _ = await departure_application(p)
    before = await phase(p)
    url, _ = database
    run_alembic("downgrade", "d061a18adea9", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert (await phase(p)).inbound_client_request_id == before.inbound_client_request_id

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
        run_alembic("downgrade", "d061a18adea9", database_url=url)
    assert "cannot discard automatic source departure handoff" in error.value.stderr


async def outcome_publisher(p, batch, scan):
    from types import SimpleNamespace as N

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    return InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository()),),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )


async def test_late_ack_cannot_downgrade_early_authoritative_success(bound):
    from sqlalchemy import select
    from test_return_allocation_postgresql import processor

    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.app.transport.models import TransportTask

    p = bound
    batch, scan, _ = await departure_application(p)
    await advance(p, batch=batch)

    class Provider:
        async def submit(self, **kwargs):
            await departure_result(p, batch, scan, publish=False)
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.departure_service.provider = Provider()
    assert await p.departure_service.submit_pending_tasks(1) == 1
    assert await p.departure_service.publish_pending_outcomes(100, await outcome_publisher(p, batch, scan)) == 1
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions() as db:
        assert (
            await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == current.departure_transport_task_id)
            )
        ).status == "SUCCEEDED"
    assert current.archived_at is not None


async def test_missing_consumer_defers_then_fresh_owner_recovers_without_reader(bound):
    from sqlalchemy import update
    from test_return_allocation_postgresql import processor

    from src.app.execution.models import InboundEvidence

    p = bound
    batch, scan, flow = await departure_application(p)
    await advance(p, batch=batch)
    await departure_result(p, batch, scan, publish=False)
    scan.rack_departure_flow = None
    assert await p.departure_service.publish_pending_outcomes(100, await outcome_publisher(p, batch, scan)) == 1
    await processor(p, scan=scan).process_batch()
    current = await phase(p)
    assert current.departure_result_evidence_id is None
    async with p.sessions.begin() as db:
        await db.execute(
            update(InboundEvidence)
            .where(InboundEvidence.transport_task_id == current.departure_transport_task_id)
            .values(decision_next_attempt_at=None)
        )
    fresh = type(flow)(flow.returns, flow.creator, None, clock=lambda: p.now)
    batch.rack_departure_flow = scan.rack_departure_flow = fresh
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    assert (await phase(p)).archived_at is not None


@pytest.mark.parametrize("fault", ["owner", "digest", "version"])
async def test_result_identity_drift_cannot_apply_to_current_stage(bound, fault):
    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    batch, scan, flow = await departure_application(p)
    await advance(p, batch=batch)
    await departure_result(p, batch, scan)
    current = await phase(p)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, current.departure_result_evidence_id)
        if fault == "owner":
            fact.picking_task_id = p.task.id
        elif fault == "digest":
            fact.payload_digest = "0" * 64
        else:
            fact.normalized_payload = {**fact.normalized_payload, "outcome_version": 100}
            fact.payload_digest = canonical_json_digest(fact.normalized_payload)
        assert await flow.apply_transport_result_in_session(db, line=p.line, evidence=fact) == "RECONCILING"


@pytest.mark.parametrize("other_line", [False, True])
async def test_authoritative_same_rack_takeover_closes_old_unknown_without_restarting_it(bound, other_line):
    from motion_binding_support import rack_action
    from sqlalchemy import select

    from src.app.execution.models import InboundEvidence, PositionProjection
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    batch, scan, _ = await departure_application(p)
    await advance(p, batch=batch)
    original = (await phase(p)).departure_transport_task_id
    await accept_departure(p)
    await departure_result(p, batch, scan, unknown=True)
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
                picking_task_id=p.task.id,
                normalized_payload=payload,
                payload_digest=canonical_json_digest(payload),
                apply_status="APPLIED",
                published_at=p.now,
                decision_digest="c" * 64,
            )
        )
        projection = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
            )
        )
        projection.workline_id, projection.position_unknown, projection.arrival_face = line_id, False, "B"
        projection.position_json = {"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}
        projection.source_transport_task_id, projection.source_causal_token = (
            task.transport_task_id,
            binding.causal_token,
        )
        authoritative_id = task.transport_task_id
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_transport_task_id == original and current.archived_at is not None
    async with p.sessions() as db:
        projection = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
            )
        )
        assert projection.source_transport_task_id == authoritative_id


async def test_ready_from_before_current_round_is_not_reused(bound):
    from datetime import timedelta

    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    p = bound
    batch, _, _ = await departure_application(p)
    async with p.sessions.begin() as db:
        current = await db.get(AutomaticPickingReturnMotion, (await phase(p)).id)
        current.round_started_at = p.now + timedelta(seconds=1)
    with pytest.raises(ValueError, match="predates"):
        await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is None


async def test_partial_departure_reference_is_rejected_by_database(bound):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    p = bound
    await departure_application(p)
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            await db.execute(
                update(AutomaticPickingReturnMotion)
                .where(AutomaticPickingReturnMotion.id == (await phase(p)).id)
                .values(departure_operation_id=p.departure_operation_id)
            )


@pytest.mark.parametrize("change", ["clean_source_feed", "cancel_all", "plan_only"])
async def test_explicit_ready_uses_current_service_end_without_closed_feed_history(bound, change):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from sqlalchemy import select

    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack

    p = bound
    batch, _, _ = await departure_application(p)
    source = p.motion_sources[p.face_id]
    async with p.sessions.begin() as db:
        if change == "clean_source_feed":
            await db.delete(await db.get(AutomaticPickingFeed, source.feed_id))
        elif change == "cancel_all":
            members = await db.scalars(
                select(PickingTaskBinSourceRack).where(
                    PickingTaskBinSourceRack.picking_task_id == p.task.id,
                    PickingTaskBinSourceRack.rack_id == "OFFLINE-RACK",
                )
            )
            for member in members:
                member.cancelled_evidence_id = p.task.issued_evidence_id
            (await db.get(PickingTask, p.task.id)).status = "CANCELLED"
        else:
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=p.task.id,
                    plan_revision=99,
                    rack_id="OFFLINE-RACK",
                    rack_face="B",
                    source_evidence_id=p.task.issued_evidence_id,
                    created_at=p.now,
                )
            )
    # 本轮已经冻结的来源结束事实持续归 motion，不回读可清理 Feed。
    batch.return_batch_flow.motion_reader = None
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id is not None


@pytest.mark.parametrize(
    "owner,blocked",
    [
        ("current", True),
        ("previous", False),
        ("legacy_current", True),
        ("legacy_previous", False),
        ("cancelled", False),
    ],
)
async def test_only_current_round_unfinished_feed_blocks_given_ready(bound, owner, blocked):
    from datetime import timedelta

    from automatic_picking.application.feed_model import AutomaticPickingFeed

    p = bound
    batch, _, _ = await departure_application(p)
    current = await phase(p)
    async with p.sessions.begin() as db:
        feed = await db.get(AutomaticPickingFeed, p.motion_sources[p.face_id].feed_id)
        feed.phase = "CANCELLED" if owner == "cancelled" else "REQUESTED"
        if owner in {"current", "previous"}:
            feed.inbound_client_request_id = (
                current.inbound_client_request_id if owner == "current" else "previous-round"
            )
            feed.round_started_at = current.round_started_at
            feed.round_causal_token = current.round_causal_token
            feed.source_arrival_evidence_id = current.arrival_evidence_id
        else:
            feed.created_at = current.round_started_at + timedelta(seconds=1 if owner == "legacy_current" else -1)
    batch.return_batch_flow.motion_reader = None
    await advance(p, batch=batch)
    assert ((await phase(p)).departure_transport_task_id is None) is blocked
