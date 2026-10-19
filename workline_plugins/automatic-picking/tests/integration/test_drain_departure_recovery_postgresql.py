"""9F：原 CTU03 明确未接纳后的离线持久重决策。"""

import pytest
from sqlalchemy import select
from test_drain_departure_decision_postgresql import (
    application,
    database,
    drainable,
    install,
    observed,
    pending,
    qualified,
    requests,
)
from test_drain_departure_postgresql import departures, phase
from test_drain_inbound_postgresql import publisher
from test_drain_rotation_postgresql import no_batch
from test_return_allocation_postgresql import advance, arrived
from test_returns_postgresql import processor
from test_source_rack_departure_decision_postgresql import decided

from src.app.execution.models import InboundEvidence
from src.app.transport.models import TransportTask

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def departing(p):
    await application(p)
    await no_batch(p)
    await decided(p, "READY")
    await advance(p, batch=p.app[0])
    return await phase(p)


async def rejected(p, *, publish=True, apply=True):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.utils.timezone import timezone

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED, kwargs["transport_task_id"], reason_code="OFFLINE_NOT_ADMITTED"
            )

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(1) == 1
    if publish:
        assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
        if apply:
            assert await processor(p, scan=p.app[1]).process_batch() >= 1
    p.now = timezone.now_for_db()


async def test_rejection_in_place_redecides_then_wait_and_ready_resume_original_owner(drainable):
    p = drainable
    original = await departing(p)
    await rejected(p)
    install(p)
    await advance(p, batch=p.app[0])
    current = await phase(p)
    assert len(await requests(p)) == 2
    assert current.id == original.id and current.archived_at is None
    assert current.departure_operation_id != original.departure_operation_id
    assert current.departure_transport_task_id is None and current.departure_result_evidence_id is None
    async with p.sessions() as db:
        old = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == original.departure_transport_task_id)
        )
        assert old.status == "REJECTED" and old.outcome_version == old.published_outcome_version == 1
    await decided(p)
    await advance(p, batch=p.app[0])
    p.now = (await phase(p)).departure_retry_at
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 3
    await decided(p, "READY", publish=False)
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 1
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 2
    assert (await phase(p)).departure_transport_task_id != original.departure_transport_task_id


async def test_concurrent_scans_replace_rejected_request_once(drainable):
    import asyncio

    p = drainable
    await departing(p)
    await rejected(p)
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(6)))
    assert len(await requests(p)) == 2
    assert (await phase(p)).departure_operation_id == (await requests(p))[-1].operation_id
    assert (await phase(p)).departure_transport_task_id is None


@pytest.mark.parametrize("crash_at", ["request", "cursor"])
async def test_new_request_and_replaced_references_rollback_together(drainable, crash_at):
    p = drainable
    await departing(p)
    await rejected(p)
    original = await phase(p)
    flow = p.app[2].inbound.departure
    target, name = (flow.scheduler, "create_in_session") if crash_at == "request" else (flow, "_request")
    previous = getattr(target, name)

    async def crash(*args, **kwargs):
        await previous(*args, **kwargs)
        raise RuntimeError("offline redecision crash")

    setattr(target, name, crash)
    with pytest.raises(RuntimeError, match="offline redecision crash"):
        await advance(p, batch=p.app[0])
    saved = await phase(p)
    assert saved.departure_operation_id == original.departure_operation_id
    assert saved.departure_transport_task_id == original.departure_transport_task_id
    assert saved.departure_result_evidence_id == original.departure_result_evidence_id
    assert len(await requests(p)) == 1
    install(p)
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("new_transport", [False, True])
async def test_old_ready_and_rejection_replay_do_not_change_current_identity(drainable, new_transport):
    p = drainable
    await departing(p)
    old_request = (await requests(p))[0]
    await rejected(p)
    rejection_id = (await phase(p)).departure_result_evidence_id
    await advance(p, batch=p.app[0])
    if new_transport:
        await decided(p, "READY")
        await advance(p, batch=p.app[0])
    current = await phase(p)
    flow = p.app[2].inbound.departure
    async with p.sessions.begin() as db:
        assert (
            await flow.apply_decision_result_in_session(
                db, line=p.line, evidence=await db.get(InboundEvidence, old_request.response_evidence_id)
            )
            == "DRAIN_DEPARTURE_OLD_DECISION"
        )
        assert (
            await flow.apply_transport_result_in_session(
                db, line=p.line, evidence=await db.get(InboundEvidence, rejection_id)
            )
            == "IGNORED"
        )
    await advance(p, batch=p.app[0])
    saved = await phase(p)
    assert saved.departure_operation_id == current.departure_operation_id
    assert saved.departure_transport_task_id == current.departure_transport_task_id
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("released", [False, True])
async def test_no_executing_and_released_window_keep_original_drain_round(drainable, released):
    from src.app.execution.models import TransportDecisionBinding
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = drainable
    original = await departing(p)
    await rejected(p)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        arrival = await db.get(InboundEvidence, original.arrival_evidence_id)
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == arrival.transport_task_id)
        )
        window = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == task.client_request_id)
        )
        if released:
            window.window_released_at = p.now
    install(p)
    await advance(p, batch=p.app[0])
    current = await phase(p)
    assert current.id == original.id and current.arrival_evidence_id == original.arrival_evidence_id
    assert current.source_evidence_id == original.source_evidence_id
    assert current.departure_operation_id != original.departure_operation_id
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("owner", ["same_rack", "other_line", "independent_rack"])
async def test_unacked_owner_action_only_blocks_its_rack(drainable, owner):
    from motion_binding_support import rack_action

    p = drainable
    original = await departing(p)
    await rejected(p)
    async with p.sessions.begin() as db:
        line_id = p.line.id
        if owner == "other_line":
            line = type(p.line)(line_code="OTHER-OFFLINE", line_name="Other offline", line_type=p.line.line_type)
            db.add(line)
            await db.flush()
            line_id = line.id
        _, binding = await rack_action(
            db,
            p,
            "INDEPENDENT" if owner == "independent_rack" else "OFFLINE-DRAIN",
            "B",
            root=object(),
            status="PENDING",
            line_id=line_id,
        )
        binding.picking_task_id = None
    await advance(p, batch=p.app[0])
    current = await phase(p)
    assert len(await requests(p)) == (2 if owner == "independent_rack" else 1)
    if owner != "independent_rack":
        assert current.departure_operation_id == original.departure_operation_id
        assert current.departure_transport_task_id == original.departure_transport_task_id


@pytest.mark.parametrize(
    "incomplete", ["host_unpublished", "fact_unpublished", "position_missing", "position_unknown", "face_unknown"]
)
async def test_missing_published_rejection_or_current_authority_keeps_original_action(drainable, incomplete):
    from src.app.execution.models import PositionProjection

    p = drainable
    await departing(p)
    await rejected(p, publish=incomplete != "host_unpublished")
    original = await phase(p)
    async with p.sessions.begin() as db:
        if incomplete == "fact_unpublished":
            (await db.get(InboundEvidence, original.departure_result_evidence_id)).published_at = None
        elif incomplete != "host_unpublished":
            position = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-DRAIN"
                )
            )
            if incomplete == "position_missing":
                await db.delete(position)
            elif incomplete == "position_unknown":
                position.position_unknown = True
            else:
                position.arrival_face = None
    await advance(p, batch=p.app[0])
    current = await phase(p)
    assert current.departure_operation_id == original.departure_operation_id
    assert current.departure_transport_task_id == original.departure_transport_task_id
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("delivery", ["RECEIVED", "DELIVERY_UNKNOWN"])
async def test_accepted_or_delivery_unknown_preserves_original_identity(drainable, delivery):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    p = drainable
    original = await departing(p)

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode(delivery), kwargs["transport_task_id"])

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(1) == 1
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_transport_task_id == original.departure_transport_task_id
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("known_position", [False, True])
async def test_accepted_failed_is_outside_redecision_scope(drainable, known_position):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.core.uuid7 import new_uuid7

    p = drainable
    original = await departing(p)

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(1) == 1
    identity = new_uuid7()
    data = {
        "transport_task_id": original.departure_transport_task_id,
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
    ack = await p.inbound_service.record_callback(
        operation_id=identity, operation=message["operation"], message=message, payload=data, rejection_reason_code=None
    )
    assert ack["code"] == "RECEIVED"
    assert await p.inbound_service.process_pending_evidence(100) == 1
    assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id == original.departure_operation_id
    assert (await phase(p)).departure_transport_task_id == original.departure_transport_task_id
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("fault", ["digest", "owner"])
async def test_drifted_rejection_cannot_authorize_new_request(drainable, fault):
    p = drainable
    original = await departing(p)
    await rejected(p)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, (await phase(p)).departure_result_evidence_id)
        if fault == "digest":
            fact.payload_digest = "a" * 64
        else:
            fact.picking_task_id = p.task.id
    with pytest.raises(ValueError, match="drain departure result differs"):
        await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id == original.departure_operation_id
    assert len(await requests(p)) == 1


async def test_closed_old_departure_history_can_be_cleaned_after_new_action_handoff(drainable):
    from sqlalchemy import delete

    from src.app.execution.models import TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportMember

    p = drainable
    original = await departing(p)
    original_request = (await requests(p))[0]
    await rejected(p)
    rejected_id = (await phase(p)).departure_result_evidence_id
    await advance(p, batch=p.app[0])
    await decided(p, "READY")
    await advance(p, batch=p.app[0])
    current = await phase(p)
    async with p.sessions.begin() as db:
        old = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == original.departure_transport_task_id)
        )
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == old.transport_task_id))
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == old.client_request_id)
        )
        await db.delete(old)
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == original.departure_operation_id))
        await db.delete(await db.get(InboundEvidence, original_request.response_evidence_id))
        await db.delete(await db.get(InboundEvidence, rejected_id))
    install(p)
    await rejected(p)
    await advance(p, batch=p.app[0])
    saved = await phase(p)
    assert saved.id == original.id and saved.departure_operation_id != current.departure_operation_id
    assert saved.departure_transport_task_id is None
    assert len(await requests(p)) == 2


async def test_published_rejection_recovers_without_notification_or_executing_task(drainable):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = drainable
    original = await departing(p)
    await rejected(p, apply=False)
    assert (await phase(p)).departure_result_evidence_id is None
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await processor(p, scan=p.app[1]).process_batch()
    install(p)
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id != original.departure_operation_id
    assert len(await requests(p)) == 2


async def test_other_rack_pending_decision_does_not_block_rejected_rack(drainable):
    p = drainable
    await application(p, reservations=(("OFFLINE-DRAIN", ("A",)), ("SECOND", ("A",))))
    await no_batch(p)
    await no_batch(p)
    second = await phase(p, "SECOND")
    await decided(p, "READY", operation_id=(await phase(p)).departure_operation_id)
    await advance(p, batch=p.app[0])
    await rejected(p)
    await advance(p, batch=p.app[0])
    assert (await phase(p, "SECOND")).departure_operation_id == second.departure_operation_id
    assert (await phase(p, "SECOND")).departure_transport_task_id is None
    assert (await phase(p)).departure_transport_task_id is None
    assert len(await requests(p)) == 3


@pytest.mark.parametrize("other_line", [False, True])
async def test_authoritative_new_owner_is_not_replaced_by_old_rejection(drainable, other_line):
    from motion_binding_support import rack_action

    from src.app.execution.models import PositionProjection
    from src.utils.canonical_json import canonical_json_digest

    p = drainable
    original = await departing(p)
    await rejected(p)
    async with p.sessions.begin() as db:
        line_id = p.line.id
        if other_line:
            line = type(p.line)(line_code="TAKEOVER", line_name="Offline takeover", line_type=p.line.line_type)
            db.add(line)
            await db.flush()
            line_id = line.id
        task, binding = await rack_action(db, p, "OFFLINE-DRAIN", "B", root=object(), line_id=line_id)
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
                source_identity="offline-takeover:" + task.transport_task_id,
                workline_id=line_id,
                transport_task_id=task.transport_task_id,
                normalized_payload=payload,
                payload_digest=canonical_json_digest(payload),
                apply_status="APPLIED",
                published_at=p.now,
                received_at=p.now,
                decision_digest="c" * 64,
            )
        )
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-DRAIN"
            )
        )
        position.workline_id, position.position_unknown, position.arrival_face = line_id, False, "B"
        position.position_json = {"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}
        position.source_transport_task_id, position.source_causal_token = task.transport_task_id, binding.causal_token
    install(p)
    await advance(p, batch=p.app[0])
    current = await phase(p)
    assert current.departure_operation_id == original.departure_operation_id
    assert current.departure_transport_task_id == original.departure_transport_task_id
    assert len(await requests(p)) == 1
    async with p.sessions() as db:
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-DRAIN"
            )
        )
        assert position.source_transport_task_id == task.transport_task_id
