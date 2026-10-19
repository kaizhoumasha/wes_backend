"""原离场明确未接纳后重新决定；使用隔离 PostgreSQL 与离线权威输入。"""

import pytest
from sqlalchemy import select
from test_return_allocation_postgresql import advance, arrived, database, observed, pending, processor, qualified
from test_return_before_motion_postgresql import phase
from test_return_motion_binding_postgresql import bound
from test_source_rack_departure_decision_postgresql import decided, decision_application, requests
from test_source_rack_departure_postgresql import outcome_publisher

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def departing(p):
    batch, scan, flow = await decision_application(p)
    await advance(p, batch=batch)
    await decided(p, "READY")
    await advance(p, batch=batch)
    flow.reader = None
    return batch, scan, flow


async def rejected(p, batch, scan, *, publish=True, apply=True):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.utils.timezone import timezone

    class Provider:
        async def submit(self, **kwargs):
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED, kwargs["transport_task_id"], reason_code="OFFLINE_NOT_ADMITTED"
            )

    p.departure_service.provider = Provider()
    assert await p.departure_service.submit_pending_tasks(1) == 1
    if publish:
        assert await p.departure_service.publish_pending_outcomes(100, await outcome_publisher(p, batch, scan)) == 1
        if apply:
            assert await processor(p, scan=scan).process_batch() >= 1
    p.now = timezone.now_for_db()


async def test_explicit_rejection_in_place_creates_new_decision_then_wait_and_ready_recover(bound):
    from datetime import timedelta

    from src.app.transport.models import TransportTask

    p = bound
    batch, scan, _ = await departing(p)
    original = await phase(p)
    await rejected(p, batch, scan)
    await advance(p, batch=batch)
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
    await advance(p, batch=batch)
    deadline = (await phase(p)).departure_retry_at
    p.now = deadline - timedelta(milliseconds=1)
    await advance(p, batch=batch)
    assert len(await requests(p)) == 2
    p.now = deadline
    await advance(p, batch=batch)
    assert len(await requests(p)) == 3
    await decided(p, "READY")
    await advance(p, batch=batch)
    latest = await phase(p)
    assert latest.departure_transport_task_id not in (None, original.departure_transport_task_id)
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id == latest.departure_transport_task_id


async def test_concurrent_rejection_recovery_replaces_current_request_once(bound):
    import asyncio

    p = bound
    batch, scan, _ = await departing(p)
    await rejected(p, batch, scan)
    await asyncio.gather(*(advance(p, batch=batch) for _ in range(6)))
    current = await phase(p)
    assert len(await requests(p)) == 2
    assert current.departure_operation_id == (await requests(p))[-1].operation_id
    assert current.departure_transport_task_id is None


@pytest.mark.parametrize("crash_at", ["request", "cursor"])
async def test_redecision_and_cursor_replacement_rollback_together(bound, crash_at):
    p = bound
    batch, scan, flow = await departing(p)
    await rejected(p, batch, scan)
    original = await phase(p)
    if crash_at == "request":
        target, name = flow.scheduler, "create_in_session"
    else:
        target, name = flow, "_request"
    previous = getattr(target, name)

    async def crash(*args, **kwargs):
        await previous(*args, **kwargs)
        raise RuntimeError("offline transaction crash")

    setattr(target, name, crash)
    with pytest.raises(RuntimeError, match="offline transaction crash"):
        await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.departure_operation_id == original.departure_operation_id
    assert saved.departure_transport_task_id == original.departure_transport_task_id
    assert saved.departure_result_evidence_id == original.departure_result_evidence_id
    assert len(await requests(p)) == 1
    setattr(target, name, previous)
    await advance(p, batch=batch)
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("after_transport", [False, True])
async def test_old_ready_and_rejected_result_replay_never_replace_current_cursor(bound, after_transport):
    from src.app.execution.models import InboundEvidence

    p = bound
    batch, scan, flow = await departing(p)
    original = await phase(p)
    original_request = (await requests(p))[0]
    await rejected(p, batch, scan)
    rejection = (await phase(p)).departure_result_evidence_id
    await advance(p, batch=batch)
    if after_transport:
        await decided(p, "READY")
        await advance(p, batch=batch)
    current = await phase(p)
    async with p.sessions.begin() as db:
        assert (
            await flow.apply_decision_result_in_session(
                db, line=p.line, evidence=await db.get(InboundEvidence, original_request.response_evidence_id)
            )
            == "SOURCE_DEPARTURE_OLD_DECISION"
        )
        assert (
            await flow.apply_transport_result_in_session(
                db, line=p.line, evidence=await db.get(InboundEvidence, rejection)
            )
            == "IGNORED"
        )
    await advance(p, batch=batch)
    saved = await phase(p)
    assert saved.departure_operation_id == current.departure_operation_id
    assert saved.departure_transport_task_id == current.departure_transport_task_id
    assert len(await requests(p)) == 2
    assert original.departure_operation_id != saved.departure_operation_id


@pytest.mark.parametrize("released", [False, True])
async def test_cross_revision_and_no_executing_parent_keep_original_recovery_owner(bound, released):
    from src.app.execution.models import TransportDecisionBinding
    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack

    p = bound
    batch, scan, _ = await departing(p)
    original = await phase(p)
    await rejected(p, batch, scan)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        if released:
            root = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == original.inbound_client_request_id
                )
            )
            root.window_released_at = p.now
        db.add(
            PickingTaskBinSourceRack(
                picking_task_id=p.task.id,
                plan_revision=99,
                rack_id="OFFLINE-RACK",
                rack_face="B",
                source_evidence_id=p.task.issued_evidence_id,
            )
        )
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.inbound_client_request_id == original.inbound_client_request_id
    assert current.round_started_at == original.round_started_at
    assert current.round_causal_token == original.round_causal_token
    assert len(await requests(p)) == 2 and current.archived_at is None


@pytest.mark.parametrize("other_line", [False, True])
async def test_authoritative_new_round_takeover_closes_old_rejection_without_new_decision(bound, other_line):
    from test_source_rack_departure_decision_postgresql import given_takeover

    p = bound
    batch, scan, _ = await departing(p)
    original = await phase(p)
    await rejected(p, batch, scan)
    await given_takeover(p, other_line=other_line)
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.archived_at is not None
    assert current.departure_operation_id == original.departure_operation_id
    assert len(await requests(p)) == 1
    await advance(p, batch=batch)
    assert len(await requests(p)) == 1


@pytest.mark.parametrize("same_rack", [False, True])
async def test_pending_takeover_holds_same_rack_but_not_independent_rack(bound, same_rack):
    from motion_binding_support import rack_action

    p = bound
    batch, scan, _ = await departing(p)
    await rejected(p, batch, scan)
    async with p.sessions.begin() as db:
        await rack_action(db, p, "OFFLINE-RACK" if same_rack else "INDEPENDENT", "B", root=object(), status="PENDING")
    await advance(p, batch=batch)
    assert len(await requests(p)) == (1 if same_rack else 2)


@pytest.mark.parametrize(
    "incomplete",
    ["host_unpublished", "fact_unpublished", "fact_unapplied", "position_missing", "position_unknown", "face_unknown"],
)
async def test_incomplete_rejection_or_current_position_never_authorizes_redecision(bound, incomplete):
    from src.app.execution.models import InboundEvidence, PositionProjection

    p = bound
    batch, scan, _ = await departing(p)
    await rejected(p, batch, scan, publish=incomplete != "host_unpublished")
    original = await phase(p)
    async with p.sessions.begin() as db:
        if incomplete.startswith("fact_"):
            fact = await db.get(InboundEvidence, original.departure_result_evidence_id)
            if incomplete == "fact_unpublished":
                fact.published_at = None
            else:
                fact.apply_status = "PENDING"
        elif incomplete.startswith("position_") or incomplete == "face_unknown":
            position = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK", PositionProjection.object_id == "OFFLINE-RACK"
                )
            )
            if incomplete == "position_missing":
                await db.delete(position)
            elif incomplete == "position_unknown":
                position.position_unknown = True
            else:
                position.arrival_face = None
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id == original.departure_operation_id
    assert current.departure_transport_task_id == original.departure_transport_task_id
    assert current.archived_at is None and len(await requests(p)) == 1


@pytest.mark.parametrize("delivery", ["pending", "RECEIVED", "DELIVERY_UNKNOWN"])
async def test_pending_accepted_or_delivery_unknown_does_not_create_new_business_request(bound, delivery):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    p = bound
    batch, _, _ = await departing(p)
    original = await phase(p)
    if delivery != "pending":

        class Provider:
            async def submit(self, **kwargs):
                return TransportSubmitResult(TransportSubmitCode(delivery), kwargs["transport_task_id"])

        p.departure_service.provider = Provider()
        assert await p.departure_service.submit_pending_tasks(1) == 1
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id == original.departure_operation_id
    assert current.departure_transport_task_id == original.departure_transport_task_id
    assert current.archived_at is None and len(await requests(p)) == 1


@pytest.mark.parametrize("known_position", [False, True])
async def test_accepted_failed_result_is_outside_redecision_branch(bound, known_position):
    from test_source_rack_departure_postgresql import accept_departure

    from src.core.uuid7 import new_uuid7

    p = bound
    batch, scan, _ = await departing(p)
    original = await phase(p)
    await accept_departure(p)
    identity = new_uuid7()
    data = {
        "transport_task_id": original.departure_transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "OFFLINE-RACK",
        "status": "FAILED",
        "position_unknown": not known_position,
        "failure_code": "OFFLINE_FAILURE",
    }
    if known_position:
        data.update(final_position={"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}, arrival_face="A")
    message = {"operation": "transport.task.resulted@v1", "operation_id": identity, "timestamp": 1, "data": data}
    ack = await p.departure_service.record_callback(
        operation_id=identity,
        operation=message["operation"],
        message=message,
        payload=data,
        rejection_reason_code=None,
    )
    assert ack["code"] == "RECEIVED"
    assert await p.departure_service.process_pending_evidence(100) == 1
    assert await p.departure_service.publish_pending_outcomes(100, await outcome_publisher(p, batch, scan)) == 1
    assert await processor(p, scan=scan).process_batch() >= 1
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_operation_id == original.departure_operation_id
    assert current.departure_transport_task_id == original.departure_transport_task_id
    assert current.archived_at is None and len(await requests(p)) == 1


@pytest.mark.parametrize("fault", ["version", "status", "digest", "owner"])
async def test_redecision_requires_exact_original_published_rejection_identity(bound, fault):
    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    p = bound
    batch, scan, _ = await departing(p)
    await rejected(p, batch, scan)
    original = await phase(p)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, original.departure_result_evidence_id)
        payload = dict(fact.normalized_payload)
        if fault == "version":
            payload["outcome_version"] = True
        elif fault == "status":
            payload["status"] = "FAILED"
        elif fault == "owner":
            fact.picking_task_id = p.task.id
        fact.normalized_payload = payload
        fact.payload_digest = "a" * 64 if fault == "digest" else canonical_json_digest(payload)
    with pytest.raises(ValueError, match="departure rejection"):
        await advance(p, batch=batch)
    assert len(await requests(p)) == 1
    assert (await phase(p)).departure_operation_id == original.departure_operation_id


@pytest.mark.parametrize("cleanup_at", ["before_redecision", "after_redecision"])
async def test_restart_recovers_without_closed_source_or_previous_departure_history(bound, cleanup_at):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow
    from sqlalchemy import delete

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportMember, TransportTask

    p = bound
    batch, scan, flow = await departing(p)
    original = await phase(p)
    original_request = (await requests(p))[0]
    await rejected(p, batch, scan)
    rejected_id = (await phase(p)).departure_result_evidence_id
    if cleanup_at == "after_redecision":
        await advance(p, batch=batch)
    source = p.motion_sources[p.face_id]
    batch.return_batch_flow.motion_reader = None
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingFeed, source.feed_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.response_evidence_id == source.end_id))
    restarted = SourceRackDepartureFlow(flow.returns, flow.creator, None, scheduler=flow.scheduler, clock=lambda: p.now)
    batch.rack_departure_flow = scan.rack_departure_flow = restarted
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.id == original.id and current.departure_operation_id != original.departure_operation_id
    assert current.departure_transport_task_id is None
    assert current.service_end_evidence_id == original.service_end_evidence_id
    async with p.sessions() as db:
        window = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == original.inbound_client_request_id
            )
        )
        old = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == original.departure_transport_task_id)
        )
        assert window.window_departure_client_request_id == old.client_request_id
    await decided(p, "READY")
    await advance(p, batch=batch)
    current = await phase(p)
    assert current.departure_transport_task_id not in (None, original.departure_transport_task_id)
    if cleanup_at == "after_redecision":
        async with p.sessions.begin() as db:
            window = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == original.inbound_client_request_id
                )
            )
            latest = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == current.departure_transport_task_id)
            )
            assert window.window_departure_client_request_id == latest.client_request_id
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == old.transport_task_id))
            await db.execute(
                delete(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == old.client_request_id
                )
            )
            await db.delete(await db.get(TransportTask, old.id))
            await db.execute(
                delete(WmsConfirmation).where(WmsConfirmation.operation_id == original.departure_operation_id)
            )
            await db.delete(await db.get(InboundEvidence, original_request.response_evidence_id))
            await db.delete(await db.get(InboundEvidence, rejected_id))
        batch.rack_departure_flow = scan.rack_departure_flow = SourceRackDepartureFlow(
            flow.returns, flow.creator, None, scheduler=flow.scheduler, clock=lambda: p.now
        )
        await rejected(p, batch, scan)
        await advance(p, batch=batch)
        assert (await phase(p)).departure_operation_id != current.departure_operation_id
        assert (await phase(p)).departure_transport_task_id is None
    else:
        await advance(p, batch=batch)
        assert (await phase(p)).departure_transport_task_id == current.departure_transport_task_id


async def test_two_explicit_rejections_continue_with_fresh_decisions_and_transports(bound):
    p = bound
    batch, scan, _ = await departing(p)
    transports = {(await phase(p)).departure_transport_task_id}
    operations = {(await phase(p)).departure_operation_id}
    for _ in range(2):
        await rejected(p, batch, scan)
        await advance(p, batch=batch)
        current = await phase(p)
        assert current.departure_operation_id not in operations and current.departure_transport_task_id is None
        operations.add(current.departure_operation_id)
        await decided(p, "READY")
        await advance(p, batch=batch)
        current = await phase(p)
        assert current.departure_transport_task_id not in transports
        transports.add(current.departure_transport_task_id)
    assert len(operations) == len(transports) == len(await requests(p)) == 3


async def test_released_window_allows_cleaning_old_departure_before_new_ready(bound):
    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow
    from sqlalchemy import delete

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportMember, TransportTask

    p = bound
    batch, scan, flow = await departing(p)
    original = await phase(p)
    old_request = (await requests(p))[0]
    await rejected(p, batch, scan)
    rejected_id = (await phase(p)).departure_result_evidence_id
    async with p.sessions.begin() as db:
        root = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == original.inbound_client_request_id
            )
        )
        root.window_released_at = p.now
    await advance(p, batch=batch)
    current_operation = (await phase(p)).departure_operation_id
    assert current_operation != original.departure_operation_id
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
        await db.delete(await db.get(InboundEvidence, old_request.response_evidence_id))
        await db.delete(await db.get(InboundEvidence, rejected_id))
    batch.rack_departure_flow = scan.rack_departure_flow = SourceRackDepartureFlow(
        flow.returns, flow.creator, None, scheduler=flow.scheduler, clock=lambda: p.now
    )
    await advance(p, batch=batch)
    assert (await phase(p)).departure_operation_id == current_operation
    await decided(p, "READY")
    await advance(p, batch=batch)
    assert (await phase(p)).departure_transport_task_id not in (None, original.departure_transport_task_id)
