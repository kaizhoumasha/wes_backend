"""drain 终面及显式 departure READY 的离线事务交接。"""

import pytest
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from sqlalchemy import select
from test_drain_rotation_postgresql import (
    database,
    drainable,
    observed,
    pending,
    prepared,
    qualified,
)
from test_return_allocation_postgresql import advance, arrived

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.transport.models import TransportTask

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_installed_departure_prepares_final_face_before_return_evaluation(drainable):
    p = drainable
    await departure_application(p)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        phases = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        assert len(phases) == 1
        assert phases[0].rack_id == "OFFLINE-DRAIN"
        assert phases[0].rack_face == "A"
        assert phases[0].allocation_id is not None
        assert phases[0].closed_at is None


async def phase(p, rack="OFFLINE-DRAIN"):
    async with p.sessions() as db:
        return await db.scalar(
            select(AutomaticPickingReturnMotion).where(
                AutomaticPickingReturnMotion.rack_id == rack,
                AutomaticPickingReturnMotion.archived_at.is_(None),
            )
        )


async def given_ready(p, *, rack="OFFLINE-DRAIN", face="A", publish=True, destination_type="RACK_POSITION"):
    from test_drain_progress_postgresql import current
    from wes_plugin_sdk import TransportRackPosition, wms_operations

    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
    from src.core.uuid7 import new_uuid7
    from src.utils.canonical_json import canonical_json_digest
    from src.utils.timezone import timezone

    p.now, operation = timezone.now_for_db(), new_uuid7()
    intent = wms_operations.outbound_rack_departure_decide(
        operation_id=operation,
        task_id=None,
        rack_id=rack,
        current_location=TransportRackPosition(rack + "-WORK"),
        current_face=face,
    )
    scheduler = RackDepartureScheduler(WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService()))
    async with p.sessions.begin() as db:
        await scheduler.create_in_session(db, intent, workline_id=p.line.id, created_at=p.now)
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation))
        payload = {
            "operation_id": operation,
            "timestamp": 1,
            "code": "DECIDED",
            "data": {"result": "READY", "rack_destination": {"type": destination_type, "location_code": "OFFLINE-OUT"}},
        }
        fact = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=request.operation + ":" + operation,
            operation=request.operation,
            operation_id=operation,
            workline_id=p.line.id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            published_at=p.now if publish else None,
            received_at=p.now,
            decision_digest="c" * 64,
        )
        db.add(fact)
        await db.flush()
        request.status, request.response_evidence_id = "COMPLETED", fact.id
        request.response_result, request.completed_at = "READY", p.now
    root, stage = await current(p), await phase(p, rack)
    from automatic_picking.application.drain_departure import MatchedDrainDeparture

    p.departures = (
        *p.departures,
        MatchedDrainDeparture(
            drain_id=root.id, rack_id=rack, arrival_evidence_id=stage.arrival_evidence_id, operation_id=operation
        ),
    )
    return operation, fact.id


async def departures(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(TransportTask)
                    .where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU03")
                    .order_by(TransportTask.id)
                )
            ).all()
        )


def install_departure(p):
    from automatic_picking.application.drain_departure import DrainDepartureFlow

    class Reader:
        async def read_in_session(self, db, *, workline_id):
            return p.departures

    p.app[2].inbound.departure = DrainDepartureFlow(p.app[2].inbound.rotation, Reader(), clock=lambda: p.now)


async def departure_application(p, *, faces=("A",), reservations=None):
    await prepared(p, faces=faces, reservations=reservations)
    p.departures = ()
    install_departure(p)
    return p.app


async def test_frozen_ready_creates_one_ctu03_with_original_decision_and_window(drainable):
    from test_drain_rotation_postgresql import no_batch

    from src.app.execution.models import TransportDecisionBinding

    p = drainable
    await departure_application(p)
    await no_batch(p)
    operation, evidence_id = await given_ready(p)
    await advance(p, batch=p.app[0])
    await advance(p, batch=p.app[0])
    tasks = await departures(p)
    assert len(tasks) == 1
    task, stage = tasks[0], await phase(p)
    assert stage.departure_transport_task_id == task.transport_task_id
    assert stage.departure_operation_id == operation
    assert task.request_json["target"] == {"kind": "RACK_POSITION", "location_code": "OFFLINE-OUT"}
    async with p.sessions() as db:
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == task.client_request_id)
        )
        assert binding.picking_task_id is None
        assert binding.source_evidence_id == evidence_id
        assert binding.correlation_id == "departure:" + operation
        windows = (
            await db.scalars(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.window_departure_client_request_id == task.client_request_id
                )
            )
        ).all()
        assert len(windows) == 1 and windows[0].window_released_at is None


async def departure_result(p, task=None, *, publish=True, process=True, unknown=False):
    from test_drain_inbound_postgresql import publisher
    from test_returns_postgresql import processor

    from src.core.uuid7 import new_uuid7

    task = task or (await departures(p))[0]
    identity = new_uuid7()
    data = {
        "transport_task_id": task.transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": task.request_json["rack_id"],
        "status": "FAILED" if unknown else "SUCCEEDED",
        "position_unknown": unknown,
    }
    if unknown:
        data["failure_code"] = "OFFLINE_UNKNOWN"
    else:
        data.update(final_position={"kind": "RACK_POSITION", "location_code": "OFFLINE-OUT"}, arrival_face="A")
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
    if publish:
        assert await service.publish_pending_outcomes(100, await publisher(p)) == 1
        if process:
            await processor(p, scan=p.app[1]).process_batch()


async def created(p, *, destination_type="RACK_POSITION"):
    from test_drain_rotation_postgresql import no_batch

    await departure_application(p)
    await no_batch(p)
    await given_ready(p, destination_type=destination_type)
    await advance(p, batch=p.app[0])
    return (await departures(p))[0]


async def test_concurrent_scans_create_one_departure(drainable):
    import asyncio

    from test_drain_rotation_postgresql import no_batch

    p = drainable
    await departure_application(p)
    await no_batch(p)
    await given_ready(p)
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(3)))
    assert len(await departures(p)) == 1


async def test_crash_between_action_and_stage_binding_rolls_back_every_reference(drainable):
    from test_drain_rotation_postgresql import no_batch

    from src.app.execution.models import TransportDecisionBinding

    p = drainable
    await departure_application(p)
    await no_batch(p)
    await given_ready(p)
    creator = p.app[2].inbound.rotation.creator
    original = creator.create_source_return

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline transaction crash")

    creator.create_source_return = crash
    with pytest.raises(RuntimeError, match="offline transaction crash"):
        await advance(p, batch=p.app[0])
    assert not await departures(p)
    stage = await phase(p)
    assert stage.departure_operation_id is stage.departure_transport_task_id is None
    async with p.sessions() as db:
        assert not (
            await db.scalars(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.window_departure_client_request_id.is_not(None)
                )
            )
        ).all()
    creator.create_source_return = original
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 1


async def test_accepted_releases_window_but_preserves_drain_and_original_physical_duty(drainable):
    from test_drain_progress_postgresql import current

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    p = drainable
    task = await created(p)

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(1) == 1
    async with p.sessions() as db:
        task = await db.get(TransportTask, task.id)
        window = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.window_departure_client_request_id == task.client_request_id
            )
        )
        assert task.status == "ACCEPTED"
        assert window.window_released_at is not None
    p.departures = ()
    install_departure(p)
    await advance(p, batch=p.app[0])
    assert (await phase(p)).archived_at is None
    assert (await current(p)).archived_at is None
    assert len(await departures(p)) == 1


@pytest.mark.parametrize("delivery", ["normal", "host_unpublished", "business_unpublished", "unknown", "zone"])
async def test_callback_before_ack_only_closes_original_published_success(delivery, drainable):
    from test_drain_progress_postgresql import current

    p = drainable
    task = await created(p, destination_type="ZONE" if delivery == "zone" else "RACK_POSITION")
    old = await phase(p)
    await departure_result(
        p,
        task,
        publish=delivery != "host_unpublished",
        process=delivery != "business_unpublished",
        unknown=delivery == "unknown",
    )
    p.departures = ()
    install_departure(p)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        stage = await db.get(AutomaticPickingReturnMotion, old.id)
        assert (stage.archived_at is not None) == (delivery in {"normal", "zone"})
        if stage.archived_at is not None:
            assert stage.departure_result_evidence_id is not None
    assert (await current(p)).archived_at is None
    assert len(await departures(p)) == 1


async def test_no_executing_task_and_no_new_candidate_still_recovers_original_result(drainable):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = drainable
    task = await created(p)
    old = await phase(p)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    p.departures = ()
    install_departure(p)
    await departure_result(p, task)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnMotion, old.id)).archived_at is not None


@pytest.mark.parametrize("boundary", ["request_pending", "ready_unpublished", "wrong_face", "old_drain", "old_arrival"])
async def test_noncurrent_or_unpublished_ready_cannot_create_departure(boundary, drainable):
    from dataclasses import replace

    from test_drain_rotation_postgresql import no_batch

    p = drainable
    await departure_application(p)
    await no_batch(p)
    operation, _ = await given_ready(
        p, face="B" if boundary == "wrong_face" else "A", publish=boundary != "ready_unpublished"
    )
    if boundary == "request_pending":
        async with p.sessions.begin() as db:
            request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation))
            request.status = "PENDING"
    elif boundary == "old_drain":
        p.departures = (replace(p.departures[0], drain_id=p.departures[0].drain_id + 1),)
    elif boundary == "old_arrival":
        p.departures = (replace(p.departures[0], arrival_evidence_id=p.departures[0].arrival_evidence_id + 1),)
    if boundary == "wrong_face":
        with pytest.raises(ValueError, match="exact completed face"):
            await advance(p, batch=p.app[0])
    else:
        await advance(p, batch=p.app[0])
    assert not await departures(p)


async def test_drain_and_source_departure_have_distinct_persistent_owners(drainable):
    from automatic_picking.application.return_motion_repository import ReturnMotionRepository
    from automatic_picking.application.source_rack_departure_repository import SourceRackDepartureRepository

    p = drainable
    await created(p)
    async with p.sessions() as db:
        assert not await SourceRackDepartureRepository(ReturnMotionRepository()).pending(db, p.line.id)
        own = await p.app[2].inbound.departure.repository.pending(db, p.line.id)
        assert len(own) == 1


async def test_second_rack_proceeds_while_first_departure_result_unknown(drainable):
    from test_drain_rotation_postgresql import no_batch

    p = drainable
    await departure_application(p, reservations=(("OFFLINE-DRAIN", ("A",)), ("SECOND", ("A",))))
    await no_batch(p)
    await no_batch(p)
    await given_ready(p)
    await advance(p, batch=p.app[0])
    first = (await departures(p))[0]
    await departure_result(p, first, unknown=True)
    await given_ready(p, rack="SECOND")
    await advance(p, batch=p.app[0])
    assert [t.request_json["rack_id"] for t in await departures(p)] == ["OFFLINE-DRAIN", "SECOND"]


@pytest.mark.parametrize("obligation", ["unknown", "unpublished", "ready_without_move", "accepted"])
async def test_final_face_direct_return_obligation_blocks_departure(obligation, drainable):
    from test_drain_return_postgresql import requests
    from test_return_allocation_postgresql import result as batch_result
    from test_returns_postgresql import processor

    from src.app.execution.models import TransportDecisionBinding

    p = drainable
    await departure_application(p)
    await advance(p, batch=p.app[0])
    await given_ready(p)
    if obligation != "unknown":
        await batch_result(p)
        if obligation != "unpublished":
            await processor(p, scan=p.app[1]).process_batch()
        if obligation == "accepted":
            await advance(p, batch=p.app[0])
            async with p.sessions.begin() as db:
                binding = await db.scalar(
                    select(TransportDecisionBinding).where(TransportDecisionBinding.step == "AUTOMATIC_PICKING_RETURN")
                )
                task = await db.scalar(
                    select(TransportTask).where(TransportTask.client_request_id == binding.client_request_id)
                )
                task.status = "ACCEPTED"
    await advance(p, batch=p.app[0])
    assert (await phase(p)).closed_at is None
    assert not await departures(p)
    assert len(await requests(p)) == 1


async def test_rotated_final_face_hands_off_without_reading_cleaned_previous_face(drainable):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from sqlalchemy import delete
    from test_drain_return_postgresql import requests
    from test_drain_rotation_postgresql import no_batch, rotation_result, rotations

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportMember

    p = drainable
    await departure_application(p, faces=("A", "C", "B"))
    await no_batch(p)
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    await advance(p, batch=p.app[0])
    old = (await rotations(p))[0]
    old_stage = await phase(p)
    await no_batch(p)
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingReturnMotion).where(AutomaticPickingReturnMotion.id == old_stage.id))
        allocation = await db.scalar(
            select(AutomaticPickingReturnAllocation).where(
                AutomaticPickingReturnAllocation.arrival_evidence_id == old_stage.arrival_evidence_id
            )
        )
        await db.delete(allocation)
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == allocation.operation_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == old.transport_task_id))
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == old.client_request_id)
        )
        await db.execute(delete(TransportTask).where(TransportTask.id == old.id))
    install_departure(p)
    await no_batch(p)
    await given_ready(p, face="B")
    await advance(p, batch=p.app[0])
    task = (await departures(p))[0]
    await departure_result(p, task)
    await advance(p, batch=p.app[0])
    assert [r.request_payload["data"]["rack_face"] for r in await requests(p)] == ["A", "B"]
    assert len(await departures(p)) == 1


async def test_closed_final_evaluation_does_not_reopen_for_new_fifo_candidate(drainable):
    from test_drain_progress_postgresql import additional_return, current
    from test_drain_return_postgresql import requests
    from test_drain_rotation_postgresql import no_batch

    p = drainable
    await departure_application(p)
    await no_batch(p)
    root = await current(p)
    await additional_return(p, bin_code="A000000099", timestamp=9999)
    await given_ready(p)
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == len(await departures(p)) == 1
    assert (await current(p)).return_ids == root.return_ids


async def test_newer_unacked_same_rack_action_blocks_old_departure(drainable):
    from test_drain_progress_postgresql import current
    from test_drain_rotation_postgresql import no_batch
    from wes_plugin_sdk import TransportRackPosition

    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator

    p = drainable
    await departure_application(p)
    await no_batch(p)
    await given_ready(p)
    async with p.sessions.begin() as db:
        await ReliableRackTransportCreator(p.inbound_service).create_rotate(
            db,
            workline_id=p.line.id,
            picking_task_id=None,
            source_evidence_id=(await current(p)).response_evidence_id,
            correlation_id="offline-newer-owner",
            step="OFFLINE_OTHER_OWNER",
            rack_id="OFFLINE-DRAIN",
            position=TransportRackPosition("OFFLINE-DRAIN-WORK"),
            target_face="B",
        )
    await advance(p, batch=p.app[0])
    assert not await departures(p)


async def test_result_application_crash_replays_original_fact_without_second_action(drainable):
    from test_returns_postgresql import processor

    p = drainable
    task = await created(p)
    old = await phase(p)
    await departure_result(p, task, process=False)
    flow = p.app[2].inbound.departure
    original = flow.repository.observe

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline result application crash")

    flow.repository.observe = crash
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_result_evidence_id is None
    flow.repository.observe = original
    async with p.sessions.begin() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.transport_task_id == task.transport_task_id, InboundEvidence.kind == "TRANSPORT_RESULT"
            )
        )
        fact.decision_next_attempt_at = None
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        stage = await db.get(AutomaticPickingReturnMotion, old.id)
        assert stage.archived_at is not None
        assert stage.departure_transport_task_id == task.transport_task_id
    assert len(await departures(p)) == 1


async def test_late_replay_of_closed_departure_cannot_create_or_change_action(drainable):
    p = drainable
    task = await created(p)
    await departure_result(p, task)
    await advance(p, batch=p.app[0])
    from src.app.workline.models import WorkLine

    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.transport_task_id == task.transport_task_id, InboundEvidence.kind == "TRANSPORT_RESULT"
            )
        )
        assert (
            await p.app[2].inbound.departure.apply_transport_result_in_session(db, line=line, evidence=fact)
            == "DRAIN_DEPARTURE_OLD_OUTCOME"
        )
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 1


async def test_inactive_line_still_recovers_original_published_result(drainable):
    from src.app.workline.models import WorkLine

    p = drainable
    task = await created(p)
    old = await phase(p)
    await departure_result(p, task)
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).is_active = False
    p.departures = ()
    install_departure(p)
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnMotion, old.id)).archived_at is not None
    assert len(await departures(p)) == 1


async def test_concurrent_recovery_keeps_original_result_and_action(drainable):
    import asyncio

    p = drainable
    task = await created(p)
    old = await phase(p)
    await departure_result(p, task)
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(3)))
    async with p.sessions() as db:
        stage = await db.get(AutomaticPickingReturnMotion, old.id)
        assert stage.archived_at is not None
        assert stage.departure_result_evidence_id is not None
        assert stage.departure_transport_task_id == task.transport_task_id
    assert len(await departures(p)) == 1
