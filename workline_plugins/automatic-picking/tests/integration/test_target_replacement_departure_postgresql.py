"""REPLACE 旧目标架 F01 离场；全部位置/READY 为离线权威输入，不派发。"""

import asyncio
import subprocess
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from sqlalchemy import delete, func, select
from test_material_flow_postgresql import initial, result
from test_material_flow_postgresql import processor as material_processor
from test_pickup_identity_postgresql import OTHER, TARGET, components, database, prepared
from test_target_put_postgresql import advance, current_pickup
from test_target_put_postgresql import flow as ordinary_flow
from wes_plugin_sdk import TransportRackPosition, TransportRackReference, TransportRcsTemplateId, wms_operations

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.transport.models import TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.outbound_picking.departure_typed import encode_request
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
OLD = "OLD-TARGET"
LOCATION = "OFFLINE-TRANSFER"
DESTINATION = {"kind": "ZONE", "location_code": "OFFLINE-RETURN"}


async def test_pickup_keeps_original_replacement_departure():
    assert "target_departure_transport_task_id" in AutomaticPickingPickup.model_fields


def application(p, *, creator=None):
    from automatic_picking.application.target_preparation import AutomaticPickingTargetFlow
    from automatic_picking.application.target_replacement import AutomaticPickingTargetReplacementFlow

    class Reader:
        async def read_in_session(self, db, **kwargs):
            return p.observed

    base = ordinary_flow(p)
    replacement = AutomaticPickingTargetReplacementFlow(creator or p.creator, Reader())
    return AutomaticPickingTargetFlow(
        base.repository,
        base.commands,
        base.material_reader,
        base.target_reader,
        base.request_factory,
        replacement_flow=replacement,
    )


async def callback(p, task, *, publish=True, status="SUCCEEDED"):
    from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow
    from automatic_picking.application.work_repository import WorkRepository
    from test_return_allocation_postgresql import processor as scan_processor

    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    data = {
        "transport_task_id": task.transport_task_id,
        "kind": task.kind,
        "outcome_revision": 1,
        "rack_id": task.request_json["rack_id"],
        "status": status,
    }
    if status == "SUCCEEDED":
        data["final_position"] = task.request_json.get("target", task.request_json.get("position"))
        if data["final_position"]["kind"] == "ZONE":
            data["final_position"] = {"kind": "RACK_POSITION", "location_code": "OFFLINE-RETURN-POSITION"}
        data["arrival_face"] = task.request_json.get("target_face") or "B"
    else:
        data.update(failure_code="OFFLINE_UNKNOWN", position_unknown=True)
    operation_id = new_uuid7()
    message = {"operation": "transport.task.resulted@v1", "operation_id": operation_id, "timestamp": 1, "data": data}
    ack = await p.service.record_callback(
        operation_id=operation_id,
        operation=message["operation"],
        message=message,
        payload=data,
        rejection_reason_code=None,
    )
    assert ack["code"] == "RECEIVED"
    assert await p.service.process_pending_evidence(100) == 1
    scan = AutomaticPickingScanFlow(
        WorkRepository(),
        None,
        None,
        components(p)[1],
        batch_driver=AutomaticPickingBatchDriver(None, None, None, None, None, target_flow=application(p)),
    )
    plugin = build_plugin(scan_flow=scan, batch_driver=scan.batch_driver, works=WorkRepository())
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (plugin,),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    if publish:
        assert await p.service.publish_pending_outcomes(100, publisher) == 1
        assert await scan_processor(p, scan=scan).process_batch() >= 1
    return publisher, scan


@pytest_asyncio.fixture
async def replacing(prepared, request):
    from automatic_picking.application.target_replacement import MatchedTargetDeparture

    p = prepared
    p.pickup_id, p.scan_id, p.material_id, p.decide = await initial(p)
    p.accept_id = await result(
        p,
        p.decide,
        data={
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)},
            "target_preparation": {"mode": "REPLACE"},
            **({} if getattr(request, "param", None) == "no_next_source" else {"next_source_action": asdict(OTHER)}),
        },
    )
    assert await material_processor(p).process_batch() == 1
    window = RackInboundWindowService()
    p.service = TransportService(
        p.sessions,
        TransportRepository(),
        N(),
        result_timeout=timedelta(seconds=420),
        progress_hook=window.on_transport_progress,
    )
    p.creator = ReliableRackTransportCreator(p.service, inbound_window=window)
    p.observed = None
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.position_bindings = {**line.position_bindings, "TRANSFER_RACK": {"location_id": LOCATION}}
        handle = await p.creator.create(
            db,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.task.issued_evidence_id,
            correlation_id="offline-target-in:" + new_uuid7(),
            step="PICKING_TASK_TARGET_RACK_IN",
            resource_fence_id=OLD,
            intent=N(
                rack_id=OLD,
                source=TransportRackReference(OLD),
                target=TransportRackPosition(LOCATION),
                target_face="B",
                rcs_template_id=TransportRcsTemplateId.F01,
            ),
        )
        p.inbound = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id)
        )
        binding = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == p.inbound.client_request_id
            )
        )
        binding.window_target_location_code = LOCATION
    await callback(p, p.inbound)
    p.now = timezone.now_for_db()
    async with p.sessions.begin() as db:
        arrival = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == p.inbound.transport_task_id)
        )
        p.arrival_id = arrival.id
        p.departure_operation_id = new_uuid7()
        request = encode_request(
            wms_operations.outbound_rack_departure_decide(
                operation_id=p.departure_operation_id,
                task_id=None,
                rack_id=OLD,
                current_location=TransportRackPosition(LOCATION),
                current_face="B",
            ),
            timestamp=1,
        )
        payload = {
            "operation_id": p.departure_operation_id,
            "timestamp": 1,
            "code": "DECIDED",
            "data": {
                "result": "READY",
                "rack_destination": {"type": "ZONE", "location_code": DESTINATION["location_code"]},
            },
        }
        fact = InboundEvidence(
            kind="WMS_RESULT",
            source_identity="offline-departure:" + p.departure_operation_id,
            operation="outbound.rack.departure_decide@v1",
            operation_id=p.departure_operation_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            workline_id=p.line.id,
            received_at=p.now,
            processed_at=p.now,
            published_at=p.now,
            decision_digest=canonical_json_digest({"fixture": "READY"}),
            apply_status="APPLIED",
        )
        db.add(fact)
        await db.flush()
        confirmation = WmsConfirmation(
            operation="outbound.rack.departure_decide@v1",
            operation_id=p.departure_operation_id,
            workline_id=p.line.id,
            request_payload=request,
            request_digest=canonical_json_digest(request),
            status="COMPLETED",
            response_evidence_id=fact.id,
            response_result="READY",
            completed_at=p.now,
            deadline_at=p.now + timedelta(minutes=5),
        )
        db.add(confirmation)
        p.ready_id = fact.id
    p.observed = MatchedTargetDeparture(
        p.line.id, p.material_id, p.accept_id, OLD, p.inbound.client_request_id, p.arrival_id, p.departure_operation_id
    )
    return p


async def departure(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        return await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == pickup.target_departure_transport_task_id)
        )


async def assert_no_followup(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.put_command_code is None and pickup.movement_confirmation_id is None
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 2


async def test_ready_creates_only_original_f01_and_restores_without_reader(replacing):
    p = replacing
    assert await advance(p, application(p)) == 1
    async with p.sessions() as db:
        fact = await db.get(InboundEvidence, p.arrival_id)
        # 初始目标到位无插件后继应用；IGNORED 不否定原 Transport 成功与原因果位置。
        assert fact.apply_status == "IGNORED"
    task = await departure(p)
    assert task.request_json["rcs_template_id"] == "F01" and task.request_json["target"] == DESTINATION
    async with p.sessions() as db:
        assert not await application(p).replacement_flow.handed_off_in_session(
            db, line_id=p.line.id, transport_id=task.transport_task_id
        )
    p.observed = None
    assert await advance(p, application(p)) == 0
    assert (await departure(p)).transport_task_id == task.transport_task_id
    await assert_no_followup(p)


async def test_concurrent_scans_create_and_link_one_departure(replacing):
    p = replacing
    assert sorted(await asyncio.gather(*(advance(p, application(p)) for _ in range(4)))) == [0, 0, 0, 1]
    await assert_no_followup(p)


@pytest.mark.parametrize("point", ["created", "linked"])
async def test_departure_and_pickup_link_roll_back_together(replacing, point):
    p = replacing
    app = application(p)
    original = p.creator.create_transfer_departure

    class Crash:
        async def create_transfer_departure(self, *args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("offline creation crash")

    if point == "created":
        app.replacement_flow.creator = Crash()
        with pytest.raises(RuntimeError, match="crash"):
            await advance(p, app)
    else:
        async with p.sessions() as db:
            with pytest.raises(RuntimeError, match="crash"):
                async with db.begin():
                    assert await app.advance_in_session(db, line_id=p.line.id, now=p.now) == 1
                    raise RuntimeError("offline link crash")
    assert await departure(p) is None
    assert await advance(p, application(p)) == 1
    await assert_no_followup(p)


@pytest.mark.parametrize("missing", ["old-round", "unpublished-ready", "unknown-position"])
async def test_unproven_current_identity_never_creates_departure(replacing, missing):
    p = replacing
    if missing == "old-round":
        p.observed = replace(p.observed, inbound_client_request_id=new_uuid7())
    else:
        async with p.sessions.begin() as db:
            if missing == "unpublished-ready":
                (await db.get(InboundEvidence, p.ready_id)).published_at = None
            else:
                position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == OLD))
                position.position_unknown = True
    if missing == "unpublished-ready":
        with pytest.raises(ValueError, match="not published"):
            await advance(p, application(p))
    else:
        assert await advance(p, application(p)) == 0
    assert await departure(p) is None


async def test_success_before_ack_uses_real_publisher_and_factprocessor(replacing):
    p = replacing
    assert await advance(p, application(p)) == 1
    task = await departure(p)
    publisher, scan = await callback(p, task, publish=False)
    async with p.sessions() as db:
        assert not await application(p).replacement_flow.repository.finished(db, p.line.id, task.transport_task_id)
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    from test_return_allocation_postgresql import processor as scan_processor

    assert await scan_processor(p, scan=scan).process_batch() >= 1
    async with p.sessions() as db:
        assert await application(p).replacement_flow.repository.finished(db, p.line.id, task.transport_task_id)
        assert await application(p).replacement_flow.handed_off_in_session(
            db, line_id=p.line.id, transport_id=task.transport_task_id
        )
        assert (await db.get(TransportTask, task.id)).submit_attempt_count == 0
    assert await advance(p, application(p)) == 0
    await assert_no_followup(p)


async def accept(p):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.service.provider = Provider()
    assert await p.service.submit_pending_tasks(1) == 1


async def test_accepted_handoff_and_history_cleanup_recover_original_departure(replacing):
    p = replacing
    assert await advance(p, application(p)) == 1
    task = await departure(p)
    await accept(p)
    async with p.sessions.begin() as db:
        assert await application(p).replacement_flow.handed_off_in_session(
            db, line_id=p.line.id, transport_id=task.transport_task_id
        )
        window = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == p.inbound.client_request_id
            )
        )
        assert window.window_released_at is not None
        await db.execute(
            delete(TransportMember).where(TransportMember.transport_task_id == p.inbound.transport_task_id)
        )
        await db.execute(delete(TransportTask).where(TransportTask.id == p.inbound.id))
        await db.execute(delete(TransportDecisionBinding).where(TransportDecisionBinding.id == window.id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == p.arrival_id))
    p.observed = None
    assert await advance(p, application(p)) == 0
    publisher, _scan = await callback(p, task)
    assert await p.service.publish_pending_outcomes(100, publisher) == 0
    assert await advance(p, application(p)) == 0
    assert (await departure(p)).transport_task_id == task.transport_task_id
    await assert_no_followup_after_cleanup(p)


async def assert_no_followup_after_cleanup(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.put_command_code is None and pickup.movement_confirmation_id is None
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 1


async def test_frozen_current_restores_original_transport_after_projection_changes(replacing):
    p = replacing
    assert await advance(p, application(p)) == 1
    task = await departure(p)
    async with p.sessions.begin() as db:
        (await current_pickup(p, db)).target_departure_transport_task_id = None
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == OLD))
        position.source_transport_task_id = "other-authoritative-arrival"
        position.source_causal_token += 1
    assert await advance(p, application(p)) == 1
    assert (await departure(p)).transport_task_id == task.transport_task_id
    async with p.sessions() as db:
        assert await db.get(TransportTask, task.id) is not None
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 2


async def test_original_accepted_unknown_result_never_recreates_departure(replacing):
    p = replacing
    assert await advance(p, application(p)) == 1
    task = await departure(p)
    await accept(p)
    await callback(p, task, status="FAILED")
    p.observed = None
    assert await advance(p, application(p)) == 0
    async with p.sessions() as db:
        flow = application(p).replacement_flow
        assert await flow.handed_off_in_session(db, line_id=p.line.id, transport_id=task.transport_task_id)
        assert not await flow.repository.finished(db, p.line.id, task.transport_task_id)
    assert (await departure(p)).transport_task_id == task.transport_task_id
    await assert_no_followup(p)


async def test_result_replay_and_foreign_identity_do_not_change_original_departure(replacing):
    p = replacing
    assert await advance(p, application(p)) == 1
    task = await departure(p)
    _, scan = await callback(p, task)
    from test_return_allocation_postgresql import processor as scan_processor

    assert await scan_processor(p, scan=scan).process_batch() == 0
    async with p.sessions.begin() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{task.transport_task_id}:outcome:1"
            )
        )
        assert (
            await application(p).apply_transport_result_in_session(db, line=p.line, evidence=fact)
            == "TARGET_REPLACEMENT_DEPARTURE_OBSERVED"
        )
        payload = {**fact.normalized_payload, "client_request_id": "FOREIGN"}
        wrong = N(
            transport_task_id=task.transport_task_id,
            kind=fact.kind,
            apply_status=fact.apply_status,
            workline_id=fact.workline_id,
            picking_task_id=None,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
        )
        assert await application(p).apply_transport_result_in_session(db, line=p.line, evidence=wrong) == "RECONCILING"
    await assert_no_followup(p)


async def test_missing_consumer_defers_same_result_until_injected(replacing):
    from test_return_allocation_postgresql import processor as scan_processor

    p = replacing
    assert await advance(p, application(p)) == 1
    task = await departure(p)
    publisher, scan = await callback(p, task, publish=False)
    scan.batch_driver.target_flow = None
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    async with p.sessions() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{task.transport_task_id}:outcome:1"
            )
        )
        assert fact.published_at is None and fact.decision_next_attempt_at is not None
        evidence_id = fact.id
    p.now += timedelta(seconds=2)
    scan.batch_driver.target_flow = application(p)
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, evidence_id)).published_at is not None
    await assert_no_followup(p)


@pytest.mark.parametrize("after_ack", [False, True])
async def test_two_pickups_share_the_same_old_arrival_departure(replacing, after_ack):
    from test_material_flow_postgresql import bind, scan
    from test_pickup_identity_postgresql import consume
    from test_rotated_target_put_postgresql import removed

    p = replacing
    assert await advance(p, application(p)) == 1
    original = await departure(p)
    if after_ack:
        await accept(p)
    await removed(p)
    pickup_id = await consume(p, p.accept_id)
    material_id = await bind(p, await scan(p, pickup_id))
    assert await material_processor(p).process_batch() == 1
    async with p.sessions() as db:
        confirmation = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.material_execution_id == material_id)
        )
    accepted = await result(
        p,
        confirmation,
        data={
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET), "slot_id": "S2", "rack_column": 2},
            "target_preparation": {"mode": "REPLACE"},
        },
    )
    assert await material_processor(p).process_batch() == 1
    p.observed = replace(p.observed, material_execution_id=material_id, accept_evidence_id=accepted)
    assert await advance(p, application(p)) == 1
    p.pickup_id = pickup_id
    assert (await departure(p)).transport_task_id == original.transport_task_id
    await assert_no_followup(p)


async def test_migration_preserves_pickup_and_refuses_losing_live_reference(replacing):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = replacing
    assert await advance(p, application(p)) == 1
    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        pickup.target_departure_confirmation_id = pickup.target_departure_round_id = (
            pickup.target_departure_retry_at
        ) = None
    url = p.sessions.kw["bind"].url.render_as_string(hide_password=False)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "f489f59e9b22", database_url=url)
    assert "cannot discard automatic target departure handoff" in error.value.stderr
    async with p.sessions.begin() as db:
        (await current_pickup(p, db)).target_departure_transport_task_id = None
    run_alembic("downgrade", "f489f59e9b22", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.pickup_id == p.pickup_id and pickup.target_departure_transport_task_id is None
    assert await advance(p, application(p)) == 1
    await assert_no_followup(p)


@pytest.mark.parametrize("released,published", [(False, True), (True, True), (False, False)])
async def test_same_arrival_round_rotation_can_depart_but_released_round_cannot(replacing, released, published):
    from automatic_picking.application.target_rotation_repository import TARGET_ROTATION_STEP

    p = replacing
    async with p.sessions.begin() as db:
        handle = await p.creator.create_rotate(
            db,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.accept_id,
            correlation_id="offline-old-target-rotate",
            step=TARGET_ROTATION_STEP,
            rack_id=OLD,
            position=TransportRackPosition(LOCATION),
            target_face="A",
        )
        rotated = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id)
        )
    publisher, _ = await callback(p, rotated, publish=False)
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    async with p.sessions.begin() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == rotated.transport_task_id)
        )
        # 显式给定已发布换面成功；本片不重复原 ROTATE consumer 矩阵。
        fact.published_at = timezone.now_for_db() if published else None
        fact.decision_digest = canonical_json_digest({"fixture": "ROTATE_PUBLISHED"}) if published else None
        request = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.operation_id == p.departure_operation_id)
        )
        request.request_payload = encode_request(
            wms_operations.outbound_rack_departure_decide(
                operation_id=p.departure_operation_id,
                task_id=None,
                rack_id=OLD,
                current_location=TransportRackPosition(LOCATION),
                current_face="A",
            ),
            timestamp=1,
        )
        request.request_digest = canonical_json_digest(request.request_payload)
        request.created_at = timezone.now_for_db()
        request.completed_at = request.created_at
        ready = await db.get(InboundEvidence, p.ready_id)
        ready.received_at = ready.processed_at = ready.published_at = request.created_at
        if released:
            inbound = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == p.inbound.client_request_id
                )
            )
            inbound.window_released_at = timezone.now_for_db()
    expected = int(not released and published)
    assert await advance(p, application(p)) == expected
    assert (await departure(p) is not None) == bool(expected)


@pytest_asyncio.fixture
async def first_departure(replacing):
    p = replacing
    async with p.sessions.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == p.departure_operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == p.ready_id))
    p.observed = replace(p.observed, departure_operation_id=None)
    return p


async def test_first_departure_request_and_current_are_atomic_and_unique(first_departure):
    p = first_departure
    assert sorted(await asyncio.gather(*(advance(p, decision_application(p)) for _ in range(3)))) == [0, 0, 1]
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        current = await db.get(WmsConfirmation, pickup.target_departure_confirmation_id)
        assert current.request_payload["data"] == {
            "task_id": None,
            "rack_id": OLD,
            "current_location": {"type": "RACK_POSITION", "location_code": LOCATION},
            "current_face": "B",
        }
        assert pickup.target_departure_round_id == p.inbound.client_request_id
        assert pickup.target_departure_retry_at is None
        assert pickup.target_departure_transport_task_id is None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WmsConfirmation)
                .where(WmsConfirmation.operation == "outbound.rack.departure_decide@v1")
            )
            == 1
        )


def decision_application(p):
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService

    app = application(p)
    app.replacement_flow.scheduler = RackDepartureScheduler(
        WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService())
    )
    app.replacement_flow.clock = lambda: p.now
    return app


async def current_request(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        return await db.get(WmsConfirmation, pickup.target_departure_confirmation_id)


async def departure_response(p, request, result="WAIT", *, publish=False):
    async with p.sessions.begin() as db:
        current = await db.get(WmsConfirmation, request.id)
        data = (
            {"result": "WAIT", "retry_after_ms": 50}
            if result == "WAIT"
            else {
                "result": "READY",
                "rack_destination": {"type": "ZONE", "location_code": DESTINATION["location_code"]},
            }
        )
        payload = {"operation_id": current.operation_id, "timestamp": 1, "code": "DECIDED", "data": data}
        evidence = InboundEvidence(
            kind="WMS_RESULT",
            source_identity="departure:" + current.operation_id,
            operation=current.operation,
            operation_id=current.operation_id,
            workline_id=p.line.id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=p.now,
            processed_at=None,
            published_at=p.now if publish else None,
            decision_digest=canonical_json_digest({"fixture": result}) if publish else None,
        )
        db.add(evidence)
        await db.flush()
        current.status, current.response_evidence_id, current.response_result, current.completed_at = (
            "COMPLETED",
            evidence.id,
            result,
            p.now,
        )
        return evidence.id


async def apply_departure_fact(p, evidence_id, app=None):
    from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow
    from automatic_picking.application.work_repository import WorkRepository
    from test_return_allocation_postgresql import processor as scan_processor

    scan = AutomaticPickingScanFlow(
        WorkRepository(),
        None,
        None,
        components(p)[1],
        batch_driver=AutomaticPickingBatchDriver(
            None, None, None, None, None, target_flow=app or decision_application(p)
        ),
    )
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    return scan


async def test_wait_due_current_survives_restart_and_old_result_cannot_reopen(first_departure):
    p = first_departure
    assert await advance(p, decision_application(p)) == 1
    original = await current_request(p)
    evidence_id = await departure_response(p, original)
    assert await advance(p, decision_application(p)) == 0  # 未发布不续问。
    await apply_departure_fact(p, evidence_id)
    async with p.sessions() as db:
        due = (await current_pickup(p, db)).target_departure_retry_at
        assert due == p.now + timedelta(milliseconds=50)
    p.observed = None  # 新实例只靠持久current恢复，不再依赖外部首次发现。
    p.now = due - timedelta(microseconds=1)
    assert await advance(p, decision_application(p)) == 0
    p.now = due
    assert sorted(await asyncio.gather(*(advance(p, decision_application(p)) for _ in range(4)))) == [0, 0, 0, 1]
    successor = await current_request(p)
    assert successor.operation_id != original.operation_id
    assert successor.request_payload["data"] == original.request_payload["data"]
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, evidence_id)
        assert (
            await decision_application(p).replacement_flow.apply_decision_result_in_session(
                db, line=p.line, evidence=fact
            )
            == "TARGET_DEPARTURE_OLD_DECISION"
        )
        await db.delete(await db.get(WmsConfirmation, original.id))
        await db.flush()
        await db.delete(fact)
    assert await advance(p, decision_application(p)) == 0  # 当前请求未知，不制造第二次续问。
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.target_departure_confirmation_id == successor.id and pickup.target_departure_retry_at is None


@pytest.mark.parametrize("point", ["created", "linked"])
async def test_first_request_and_cursor_rollback_together(first_departure, point):
    p = first_departure
    app = decision_application(p)
    if point == "created":
        scheduler = app.replacement_flow.scheduler

        class Crash:
            async def create_in_session(self, *args, **kwargs):
                await scheduler.create_in_session(*args, **kwargs)
                raise RuntimeError("offline current crash")

        app.replacement_flow.scheduler = Crash()
        with pytest.raises(RuntimeError, match="current crash"):
            await advance(p, app)
    else:
        async with p.sessions() as db:
            with pytest.raises(RuntimeError, match="current crash"):
                async with db.begin():
                    assert await app.advance_in_session(db, line_id=p.line.id, now=p.now) == 1
                    raise RuntimeError("offline current crash")
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).target_departure_confirmation_id is None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WmsConfirmation)
                .where(WmsConfirmation.operation == "outbound.rack.departure_decide@v1")
            )
            == 0
        )
    assert await advance(p, decision_application(p)) == 1


async def test_wait_superseded_round_never_renews(first_departure):
    p = first_departure
    assert await advance(p, decision_application(p)) == 1
    evidence_id = await departure_response(p, await current_request(p))
    await apply_departure_fact(p, evidence_id)
    original = await current_request(p)
    p.now += timedelta(seconds=1)
    async with p.sessions.begin() as db:
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == OLD))
        position.source_causal_token += 1
    assert await advance(p, decision_application(p)) == 0
    assert (await current_request(p)).id == original.id


async def test_current_migration_roundtrip_and_live_downgrade_guard(first_departure):
    from tests.support.postgresql_heavy import run_alembic

    p = first_departure
    url = p.sessions.kw["bind"].url.render_as_string(hide_password=False)
    run_alembic("downgrade", "b514fdcfbefc", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert await advance(p, decision_application(p)) == 1
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "b514fdcfbefc", database_url=url)
    assert "cannot discard automatic target departure current" in error.value.stderr


async def test_two_materials_share_one_pending_request_and_wait_successor(first_departure):
    from test_material_flow_postgresql import bind, scan
    from test_pickup_identity_postgresql import consume
    from test_rotated_target_put_postgresql import removed

    p = first_departure
    assert await advance(p, decision_application(p)) == 1
    original = await current_request(p)
    await removed(p)
    next_pickup = await consume(p, p.accept_id)
    next_material = await bind(p, await scan(p, next_pickup))
    assert await material_processor(p).process_batch() == 1
    async with p.sessions() as db:
        confirmation = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.material_execution_id == next_material)
        )
    accepted = await result(
        p,
        confirmation,
        data={
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET), "slot_id": "S2", "rack_column": 2},
            "target_preparation": {"mode": "REPLACE"},
        },
    )
    assert await material_processor(p).process_batch() == 1
    p.observed = replace(p.observed, material_execution_id=next_material, accept_evidence_id=accepted)
    assert await advance(p, decision_application(p)) == 0
    evidence_id = await departure_response(p, original)
    await apply_departure_fact(p, evidence_id)
    p.observed = None
    p.now += timedelta(milliseconds=50)
    assert sorted(await asyncio.gather(*(advance(p, decision_application(p)) for _ in range(3)))) == [0, 0, 1]
    async with p.sessions() as db:
        rows = list(
            (await db.scalars(select(AutomaticPickingPickup).where(AutomaticPickingPickup.work_id == p.work.id))).all()
        )
        assert len(rows) == 2
        assert len({r.target_departure_confirmation_id for r in rows}) == 1
        assert rows[0].target_departure_confirmation_id != original.id
        assert all(r.target_departure_retry_at is None for r in rows)
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WmsConfirmation)
                .where(WmsConfirmation.operation == "outbound.rack.departure_decide@v1")
            )
            == 2
        )


async def test_wait_payload_drift_cannot_set_deadline_or_create_successor(first_departure):
    p = first_departure
    assert await advance(p, decision_application(p)) == 1
    request = await current_request(p)
    evidence_id = await departure_response(p, request, publish=True)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, evidence_id)
        fact.normalized_payload = {**fact.normalized_payload, "data": {"result": "WAIT", "retry_after_ms": 1}}
    with pytest.raises(ValueError, match="digest"):
        await advance(p, decision_application(p))
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.target_departure_retry_at is None and pickup.target_departure_confirmation_id == request.id
