"""9E：首次 departure/WAIT 的真实持久事务；不执行外部 HTTP 或物理派发。"""

import pytest
from sqlalchemy import select
from test_drain_departure_postgresql import (
    database,
    departure_application,
    departures,
    drainable,
    observed,
    pending,
    phase,
    qualified,
)
from test_drain_rotation_postgresql import no_batch
from test_return_allocation_postgresql import advance, arrived

from src.app.execution.models import InboundEvidence, WmsConfirmation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
OPERATION = "outbound.rack.departure_decide@v1"


def install(p):
    from automatic_picking.application.drain_departure import DrainDepartureFlow

    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService

    p.app[2].inbound.departure = DrainDepartureFlow(
        p.app[2].inbound.rotation,
        scheduler=RackDepartureScheduler(WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService())),
        clock=lambda: p.now,
    )


async def application(p, *, faces=("A",), reservations=None):
    from src.utils.timezone import timezone

    await departure_application(p, faces=faces, reservations=reservations)
    p.now = timezone.now_for_db()
    install(p)
    return p.app


async def requests(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(WmsConfirmation.operation == OPERATION).order_by(WmsConfirmation.id)
                )
            ).all()
        )


async def test_first_departure_and_current_reference_are_atomic_and_unique(drainable):
    p = drainable
    await application(p)
    await no_batch(p)
    await advance(p, batch=p.app[0])
    saved = await requests(p)
    assert len(saved) == 1
    stage = await phase(p)
    assert stage.departure_operation_id == saved[0].operation_id
    assert stage.departure_transport_task_id is None
    async with p.sessions() as db:
        arrival = await db.get(InboundEvidence, stage.arrival_evidence_id)
        assert saved[0].created_at >= max(arrival.received_at, stage.closed_at)
    assert saved[0].workline_id == p.line.id and saved[0].picking_task_id is None
    assert saved[0].request_payload["data"] == {
        "task_id": None,
        "rack_id": "OFFLINE-DRAIN",
        "current_location": {"type": "RACK_POSITION", "location_code": "OFFLINE-DRAIN-WORK"},
        "current_face": "A",
    }
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 1
    assert not await departures(p)


async def test_wait_application_and_publication_freeze_original_deadline(drainable):
    from datetime import timedelta

    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    evidence_id = await decided(p, publish=False)
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at == p.now + timedelta(seconds=1)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, evidence_id)).published_at is not None


async def test_published_wait_without_notification_renews_current_cursor_once(drainable):
    from datetime import timedelta

    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    old = (await phase(p)).departure_operation_id
    await decided(p)
    p.now += timedelta(seconds=1)
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id != old
    assert len(await requests(p)) == 2
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 2


async def test_wait_due_recovers_without_executing_task_or_candidate(drainable):
    from datetime import timedelta

    from automatic_picking.application.return_model import AutomaticPickingReturn
    from test_source_rack_departure_decision_postgresql import decided

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = drainable
    await application(p)
    await no_batch(p)
    await decided(p)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        for row in (await db.scalars(select(AutomaticPickingReturn))).all():
            row.archived_at = p.now
    p.now += timedelta(seconds=1)
    install(p)
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 2
    assert (await phase(p)).departure_operation_id == (await requests(p))[-1].operation_id


@pytest.mark.parametrize("when", ["first", "renewal"])
async def test_request_and_cursor_rollback_together(when, drainable):
    from datetime import timedelta

    from test_return_allocation_postgresql import result as batch_result
    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    if when == "first":
        await advance(p, batch=p.app[0])
        await batch_result(p, result="NO_BATCH")
        await processor(p, scan=p.app[1]).process_batch()
    else:
        await no_batch(p)
        await decided(p)
        p.now += timedelta(seconds=1)
    before = await phase(p)
    count = len(await requests(p))
    flow = p.app[2].inbound.departure
    original = flow.scheduler.create_in_session

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline request transaction crash")

    flow.scheduler.create_in_session = crash
    with pytest.raises(RuntimeError, match="offline request transaction crash"):
        await advance(p, batch=p.app[0])
    assert len(await requests(p)) == count
    assert (await phase(p)).departure_operation_id == before.departure_operation_id
    flow.scheduler.create_in_session = original
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == count + 1


@pytest.mark.parametrize("when", ["first", "renewal"])
async def test_concurrent_scans_only_create_one_current_request(when, drainable):
    import asyncio
    from datetime import timedelta

    from test_return_allocation_postgresql import result as batch_result
    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    if when == "first":
        await advance(p, batch=p.app[0])
        await batch_result(p, result="NO_BATCH")
        await processor(p, scan=p.app[1]).process_batch()
    else:
        await no_batch(p)
        await decided(p)
        p.now += timedelta(seconds=1)
    count = len(await requests(p))
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(3)))
    assert len(await requests(p)) == count + 1
    assert (await phase(p)).departure_operation_id == (await requests(p))[-1].operation_id


async def test_replayed_wait_cannot_extend_deadline_or_replace_current_request(drainable):
    from datetime import timedelta

    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    from src.app.workline.models import WorkLine

    p = drainable
    await application(p)
    await no_batch(p)
    evidence_id = await decided(p, publish=False)
    await processor(p, scan=p.app[1]).process_batch()
    first = await phase(p)
    p.now += timedelta(milliseconds=999)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        await p.app[1].apply_in_session(db, evidence_id, workline_id=line.id)
    assert (await phase(p)).departure_retry_at == first.departure_retry_at
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 1
    p.now += timedelta(milliseconds=1)
    await advance(p, batch=p.app[0])
    now = await phase(p)
    async with p.sessions.begin() as db:
        await p.app[1].apply_in_session(db, evidence_id, workline_id=p.line.id)
    assert (await phase(p)).departure_operation_id == now.departure_operation_id
    assert (await phase(p)).departure_retry_at is None
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("blocked", ["pending", "reconciling", "unpublished"])
async def test_unknown_or_unpublished_response_never_renews_identity(blocked, drainable):
    from datetime import timedelta

    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    first = await phase(p)
    if blocked == "unpublished":
        await decided(p, publish=False)
    elif blocked == "reconciling":
        async with p.sessions.begin() as db:
            request = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.operation_id == first.departure_operation_id)
            )
            request.status = "RECONCILING"
    p.now += timedelta(hours=1)
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id == first.departure_operation_id
    assert (await phase(p)).departure_retry_at is None
    assert len(await requests(p)) == 1
    assert not await departures(p)


async def test_ready_after_wait_rejoins_ctu03_without_new_input_or_early_publication(drainable):
    from datetime import timedelta

    from test_drain_departure_postgresql import departure_result
    from test_drain_progress_postgresql import current
    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    await decided(p)
    p.now += timedelta(seconds=1)
    await advance(p, batch=p.app[0])
    await decided(p, "READY", publish=False)
    install(p)
    await advance(p, batch=p.app[0])
    assert not await departures(p)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    task = (await departures(p))[0]
    await departure_result(p, task)
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 1
    assert len(await requests(p)) == 2
    assert (await current(p)).archived_at is None


async def test_wait_application_failure_rolls_back_deadline_and_publication(drainable):
    from datetime import timedelta

    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    evidence_id = await decided(p, publish=False)
    flow = p.app[2].inbound.departure
    original = flow._remember_wait

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline WAIT application crash")

    flow._remember_wait = crash
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at is None
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, evidence_id)
        assert fact.published_at is None
        fact.decision_next_attempt_at = None
    flow._remember_wait = original
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at == p.now + timedelta(seconds=1)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, evidence_id)).published_at is not None


async def test_drain_wait_is_not_consumed_by_source_owner(drainable):
    from datetime import timedelta

    from automatic_picking.application.source_rack_departure import SourceRackDepartureFlow
    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    from src.app.workline.models import WorkLine

    p = drainable
    await application(p)
    await no_batch(p)
    source = SourceRackDepartureFlow(p.app[0].return_batch_flow, p.app[2].inbound.creator, None)
    p.app[0].rack_departure_flow = p.app[1].rack_departure_flow = source
    evidence_id = await decided(p, publish=False)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        fact = await db.get(InboundEvidence, evidence_id)
        assert (
            await source.apply_decision_result_in_session(db, line=line, evidence=fact)
            == "SOURCE_DEPARTURE_OLD_DECISION"
        )
    assert (await phase(p)).departure_retry_at is None
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at == p.now + timedelta(seconds=1)


async def test_missing_drain_owner_defers_result_instead_of_source_fallback(drainable):
    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    evidence_id = await decided(p, publish=False)
    p.app[2].inbound.departure = None
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at is None
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, evidence_id)
        assert fact.published_at is None
        fact.decision_next_attempt_at = None
    install(p)
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at is not None


async def test_old_wait_and_confirmation_can_be_cleaned_before_current_ready(drainable):
    from datetime import timedelta

    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    old = (await requests(p))[0]
    evidence_id = await decided(p)
    p.now += timedelta(seconds=1)
    await advance(p, batch=p.app[0])
    async with p.sessions.begin() as db:
        await db.delete(await db.get(WmsConfirmation, old.id))
        await db.flush()
        await db.delete(await db.get(InboundEvidence, evidence_id))
    install(p)
    await decided(p, "READY", publish=False)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 1
    assert len(await requests(p)) == 1


async def test_real_confirmation_storage_and_fact_processor_rejoin_without_http(drainable):
    from datetime import timedelta

    from test_returns_postgresql import processor

    from src.app.execution.services import WmsConfirmationService
    from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult
    from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService

    p = drainable
    await application(p)
    await no_batch(p)

    class Adapter:
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

    adapter = Adapter()
    service = WmsConfirmationService(
        session_factory=p.sessions, adapter=adapter, workline_owner=RackDepartureOwnerService()
    )
    assert await service.dispatch_batch(limit=1, now=p.now) == 1
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at == p.now + timedelta(seconds=1)
    p.now += timedelta(seconds=1)
    await advance(p, batch=p.app[0])
    adapter.result = "READY"
    assert await service.dispatch_batch(limit=1, now=p.now) == 1
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert len(await departures(p)) == 1


async def test_one_rack_unknown_does_not_block_another_wait_renewal(drainable):
    from datetime import timedelta

    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p, reservations=(("OFFLINE-DRAIN", ("A",)), ("SECOND", ("A",))))
    await no_batch(p)
    await no_batch(p)
    before = {r.rack_id: r.departure_operation_id for r in [await phase(p), await phase(p, "SECOND")]}
    await decided(p, operation_id=before["SECOND"])
    p.now += timedelta(seconds=1)
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id == before["OFFLINE-DRAIN"]
    assert (await phase(p, "SECOND")).departure_operation_id != before["SECOND"]
    assert len(await requests(p)) == 3


async def test_newer_unacked_rack_action_preserves_wait_without_new_decision(drainable):
    from datetime import timedelta

    from test_drain_progress_postgresql import current
    from test_source_rack_departure_decision_postgresql import decided
    from wes_plugin_sdk import TransportRackPosition

    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator

    p = drainable
    await application(p)
    await no_batch(p)
    old = await phase(p)
    await decided(p)
    p.now += timedelta(seconds=1)
    root = await current(p)
    async with p.sessions.begin() as db:
        await ReliableRackTransportCreator(p.inbound_service).create_rotate(
            db,
            workline_id=p.line.id,
            source_evidence_id=root.response_evidence_id,
            correlation_id="offline-newer-owner",
            step="OFFLINE_OTHER_OWNER",
            rack_id="OFFLINE-DRAIN",
            position=TransportRackPosition("OFFLINE-DRAIN-WORK"),
            target_face="B",
        )
    await advance(p, batch=p.app[0])
    assert (await phase(p)).departure_operation_id == old.departure_operation_id
    assert len(await requests(p)) == 1


async def test_inactive_line_retains_original_wait_until_reactivated(drainable):
    from datetime import timedelta

    from test_returns_postgresql import processor
    from test_source_rack_departure_decision_postgresql import decided

    from src.app.workline.models import WorkLine

    p = drainable
    await application(p)
    await no_batch(p)
    evidence_id = await decided(p, publish=False)
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).is_active = False
    p.now += timedelta(seconds=1)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 1
    assert (await phase(p)).departure_retry_at is None
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, evidence_id)).published_at is None
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).is_active = True
    await processor(p, scan=p.app[1]).process_batch()
    assert (await phase(p)).departure_retry_at is not None
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, evidence_id)).published_at is not None


@pytest.mark.parametrize("drift", ["request_digest", "evidence_digest"])
async def test_response_identity_drift_does_not_freeze_wait(drift, drainable):
    from test_source_rack_departure_decision_postgresql import decided

    from src.app.workline.models import WorkLine

    p = drainable
    await application(p)
    await no_batch(p)
    evidence_id = await decided(p, publish=False)
    async with p.sessions.begin() as db:
        if drift == "request_digest":
            request = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.operation_id == (await phase(p)).departure_operation_id)
            )
            request.request_digest = "0" * 64
        else:
            (await db.get(InboundEvidence, evidence_id)).payload_digest = "0" * 64
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        with pytest.raises(ValueError):
            await p.app[1].apply_in_session(db, evidence_id, workline_id=line.id)
    assert (await phase(p)).departure_retry_at is None
    assert len(await requests(p)) == 1


async def test_concurrent_ready_recovery_creates_one_original_ctu03(drainable):
    import asyncio

    from test_source_rack_departure_decision_postgresql import decided

    p = drainable
    await application(p)
    await no_batch(p)
    await decided(p, "READY")
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(3)))
    assert len(await departures(p)) == 1
    assert (await phase(p)).departure_operation_id == (await requests(p))[0].operation_id


async def test_new_task_status_does_not_cancel_original_reservation_wait(drainable):
    from datetime import timedelta

    from test_drain_progress_postgresql import current
    from test_source_rack_departure_decision_postgresql import decided

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = drainable
    await application(p)
    await no_batch(p)
    original = await current(p)
    await decided(p)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "PREPARING"
    p.now += timedelta(seconds=1)
    await advance(p, batch=p.app[0])
    now = await current(p)
    assert now.id == original.id and now.operation_id == original.operation_id
    assert now.return_ids == original.return_ids and now.archived_at is None
    assert len(await requests(p)) == 2
