"""drain 冻结面序换面的离线持久交接，真实事务与结果链。"""

import pytest
from sqlalchemy import select
from test_drain_inbound_postgresql import (
    database,
    drainable,
    inbound_application,
    observed,
    pending,
    publisher,
    qualified,
    racks,
)
from test_drain_inbound_postgresql import result as inbound_result
from test_drain_progress_postgresql import application as drain_application
from test_drain_progress_postgresql import current, decided
from test_drain_return_postgresql import requests
from test_return_allocation_postgresql import advance, application, arrived
from test_return_allocation_postgresql import result as batch_result
from test_returns_postgresql import processor

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def prepared(p, *, faces=("A", "B"), reservations=None):
    from test_drain_progress_postgresql import additional_return

    reservations = reservations or (("OFFLINE-DRAIN", faces),)
    for index in range(sum(len(item[1]) for item in reservations) - 1):
        await additional_return(p, bin_code=f"A{index + 2:09d}", timestamp=2000 + index)
    batch, scan, _ = drain_application(p)
    await advance(p, batch=batch)
    eid = await decided(p, "READY")
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, eid)
        fact.normalized_payload = {
            **fact.normalized_payload,
            "data": {
                "result": "READY",
                "racks": [{"rack_id": rack, "rack_face": list(fs)} for rack, fs in reservations],
            },
        }
        fact.payload_digest = canonical_json_digest(fact.normalized_payload)
        for rack, _ in reservations:
            db.add(
                WorkLinePosition(
                    workline_id=p.line.id,
                    workline_code=p.line.line_code,
                    position_code=rack,
                    position_type="RACK_POSITION",
                    position_name="offline",
                    position_role="SMT_RETURN_RACK_POSITION",
                    allowed_rack_kind="RETURN",
                    logic_location_code=rack + "-WORK",
                    capacity=4,
                    enabled=True,
                )
            )
    await processor(p, scan=scan).process_batch()
    await advance(p, batch=batch)
    p.app = await inbound_application(p)
    from automatic_picking.application.drain_inbound import MatchedDrainInbound
    from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

    root = await current(p)
    p.inbound_inputs = {
        rack: MatchedDrainInbound(
            p.line.id,
            root.operation_id,
            root.response_evidence_id,
            rack,
            TransportRackPosition(rack + "-WORK"),
            fs[0],
            TransportRcsTemplateId.CTU01,
        )
        for rack, fs in reservations
    }
    await advance(p, batch=p.app[0])
    async with p.sessions() as db:
        tasks = (
            await db.scalars(
                select(TransportTask).where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU01")
            )
        ).all()
    for task in tasks:
        await inbound_result(p, task, location=task.request_json["target"]["location_code"])
    await advance(p, batch=p.app[0])
    p.faces = []
    return_batch, _ = application(p)
    p.app[0].return_batch_flow = return_batch.return_batch_flow
    p.app[1].return_batch_flow = return_batch.return_batch_flow
    from automatic_picking.application.drain_rotation import DrainRotationFlow

    p.app[2].inbound.rotation = DrainRotationFlow(p.app[0].return_batch_flow, p.app[2].inbound.creator)
    return p.app


async def rotations(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(TransportTask)
                    .where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU02")
                    .order_by(TransportTask.id)
                )
            ).all()
        )


async def no_batch(p):
    await advance(p, batch=p.app[0])
    await batch_result(p, result="NO_BATCH")
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])


async def test_original_face_no_batch_creates_one_next_frozen_rotation(drainable):
    p = drainable
    await prepared(p)
    await no_batch(p)
    rows = await rotations(p)
    assert len(rows) == 1
    assert rows[0].kind == "RACK_ROTATE"
    assert rows[0].request_json["target_face"] == "B"
    assert rows[0].request_json["position"] == {"kind": "RACK_POSITION", "location_code": "OFFLINE-DRAIN-WORK"}
    assert (await current(p)).returned_ids == []
    await advance(p, batch=p.app[0])
    assert len(await rotations(p)) == 1


async def rotation_result(p, task=None, *, publish=True, process=True, unknown=False, status="SUCCEEDED", face=None):
    from src.core.uuid7 import new_uuid7

    task = task or (await rotations(p))[-1]
    payload = {
        "transport_task_id": task.transport_task_id,
        "kind": "RACK_ROTATE",
        "outcome_revision": 1,
        "rack_id": task.request_json["rack_id"],
        "status": status,
        "position_unknown": unknown,
    }
    if not unknown:
        payload.update(
            final_position=task.request_json["position"], arrival_face=face or task.request_json["target_face"]
        )
    if status == "FAILED":
        payload["failure_code"] = "OFFLINE_UNKNOWN"
    identity = new_uuid7()
    message = {"operation": "transport.task.resulted@v1", "operation_id": identity, "timestamp": 1, "data": payload}
    assert (
        await p.inbound_service.record_callback(
            operation_id=identity,
            operation=message["operation"],
            message=message,
            payload=payload,
            rejection_reason_code=None,
        )
    )["code"] == "RECEIVED"
    assert await p.inbound_service.process_pending_evidence(100) == 1
    if publish:
        assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
        if process:
            await processor(p, scan=p.app[1]).process_batch()


async def rotated(p):
    await prepared(p)
    await no_batch(p)
    await rotation_result(p)
    await advance(p, batch=p.app[0])


async def test_callback_before_ack_hands_current_arrival_to_original_return_batch(drainable):
    p = drainable
    await rotated(p)
    row = (await racks(p))[0]
    async with p.sessions() as db:
        fact = await db.get(InboundEvidence, row.arrival_evidence_id)
        assert fact.transport_task_id == (await rotations(p))[0].transport_task_id
    await advance(p, batch=p.app[0])
    reqs = await requests(p)
    assert [r.request_payload["data"]["rack_face"] for r in reqs] == ["A", "B"]
    assert reqs[-1].request_payload["data"]["rack_id"] == "OFFLINE-DRAIN"
    assert (await current(p)).returned_ids == []
    assert (await current(p)).archived_at is None
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 2


@pytest.mark.parametrize("barrier", ["accepted", "host_unpublished", "business_unpublished", "unknown", "wrong_face"])
async def test_unfinished_rotation_never_exposes_next_face(barrier, drainable):
    p = drainable
    await prepared(p)
    await no_batch(p)
    task = (await rotations(p))[0]
    if barrier == "accepted":
        async with p.sessions.begin() as db:
            row = await db.get(TransportTask, task.id)
            row.status = "ACCEPTED"
    else:
        await rotation_result(
            p,
            publish=barrier != "host_unpublished",
            process=barrier != "business_unpublished",
            unknown=barrier == "unknown",
            status="FAILED" if barrier == "unknown" else "SUCCEEDED",
            face="A" if barrier == "wrong_face" else None,
        )
    for _ in range(2):
        await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None
    assert len(await requests(p)) == 1
    assert len(await rotations(p)) == 1
    assert (await current(p)).returned_ids == []


async def test_rotation_creation_and_stage_claim_roll_back_together(drainable, monkeypatch):
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion

    p = drainable
    await prepared(p)
    await advance(p, batch=p.app[0])
    await batch_result(p, result="NO_BATCH")
    await processor(p, scan=p.app[1]).process_batch()
    old = (await racks(p))[0]
    creator = p.app[2].inbound.rotation.creator
    original = creator.create_rotate

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline crash after Transport creation")

    with monkeypatch.context() as patch:
        patch.setattr(creator, "create_rotate", crash)
        with pytest.raises(RuntimeError, match="offline crash"):
            await advance(p, batch=p.app[0])
    assert not await rotations(p)
    row = (await racks(p))[0]
    assert (row.binding_id, row.arrival_evidence_id) == (old.binding_id, old.arrival_evidence_id)
    async with p.sessions() as db:
        stage = await db.scalar(select(AutomaticPickingReturnMotion))
        assert stage.closed_at is None and stage.archived_at is None
    await advance(p, batch=p.app[0])
    assert len(await rotations(p)) == 1


async def test_success_handoff_rolls_back_and_recovers_without_notification(drainable, monkeypatch):
    p = drainable
    await prepared(p)
    await no_batch(p)
    await rotation_result(p)
    repository = p.app[2].inbound.repository
    original = repository.recover_arrival

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("offline crash after arrival handoff")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "recover_arrival", crash)
        with pytest.raises(RuntimeError, match="offline crash"):
            await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is not None
    await advance(p, batch=p.app[0])
    assert len(await requests(p)) == 2


async def test_two_entry_concurrency_claims_rotated_face_once(drainable):
    import asyncio

    p = drainable
    await prepared(p)
    await advance(p, batch=p.app[0])
    await batch_result(p, result="NO_BATCH")
    await processor(p, scan=p.app[1]).process_batch()
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(3)))
    assert len(await rotations(p)) == 1
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    arrival = (await racks(p))[0].arrival_evidence_id
    p.faces = [(arrival, "OFFLINE-DRAIN", "B")]
    ordinary, _ = application(p)
    await asyncio.gather(advance(p, batch=p.app[0]), advance(p, batch=ordinary))
    assert len(await requests(p)) == 2
    await advance(p, batch=ordinary)
    assert len(await requests(p)) == 2


async def test_old_ingress_and_old_outcome_cannot_restore_previous_face(drainable):
    p = drainable
    await prepared(p)
    original = (await racks(p))[0]
    await no_batch(p)
    p.faces = [(original.arrival_evidence_id, "OFFLINE-DRAIN", "A")]
    ordinary, _ = application(p)
    await advance(p, batch=ordinary)
    assert len(await requests(p)) == 1
    async with p.sessions.begin() as db:
        old = await db.get(InboundEvidence, original.arrival_evidence_id)
        assert await p.app[2].inbound.apply_transport_result_in_session(db, line=p.line, evidence=old) == "IGNORED"
    assert (await racks(p))[0].arrival_evidence_id is None
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    p.faces = []
    await advance(p, batch=p.app[0])
    assert [r.request_payload["data"]["rack_face"] for r in await requests(p)] == ["A", "B"]


@pytest.mark.parametrize("barrier", ["pending", "ready", "accepted", "unknown"])
async def test_original_return_obligation_must_close_before_rotation(barrier, drainable):
    from test_drain_return_postgresql import movement_result
    from test_return_allocation_postgresql import allocation

    p = drainable
    await prepared(p)
    await advance(p, batch=p.app[0])
    if barrier != "pending":
        await batch_result(p)
        await processor(p, scan=p.app[1]).process_batch()
        await advance(p, batch=p.app[0])
        a = await allocation(p)
        if barrier == "accepted":
            async with p.sessions.begin() as db:
                task = await db.scalar(
                    select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id)
                )
                task.status = "ACCEPTED"
        if barrier == "unknown":
            await movement_result(p, a, unknown=True)
    await advance(p, batch=p.app[0])
    assert not await rotations(p)
    assert (await current(p)).returned_ids == []


async def test_successful_backstock_allows_rotation_without_reopening_original_phase(drainable):
    from test_drain_return_postgresql import movement_result
    from test_return_allocation_postgresql import allocation

    p = drainable
    await prepared(p)
    await advance(p, batch=p.app[0])
    await batch_result(p)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    await movement_result(p, await allocation(p))
    await advance(p, batch=p.app[0])
    assert len(await rotations(p)) == 1
    assert (await current(p)).returned_ids == [p.return_id]
    assert len(await requests(p)) == 1


async def test_no_demand_and_final_face_never_create_departure_or_close_drain(drainable):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = drainable
    await prepared(p)
    async with p.sessions.begin() as db:
        rows = (await db.scalars(select(AutomaticPickingReturn))).all()
        for row in rows:
            row.archived_at = p.now
    await advance(p, batch=p.app[0])
    assert len(await rotations(p)) == 1
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    await advance(p, batch=p.app[0])
    assert not await requests(p)
    assert len(await rotations(p)) == 1
    assert (await current(p)).returned_ids == []
    assert (await current(p)).archived_at is None
    async with p.sessions() as db:
        assert not (
            await db.scalars(
                select(TransportTask).where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU03")
            )
        ).all()


async def test_each_rack_rotates_independently_while_other_result_unknown(drainable):
    p = drainable
    await prepared(p, reservations=(("OFFLINE-DRAIN", ("A", "B")), ("OFFLINE-SECOND", ("A", "C"))))
    await no_batch(p)
    first = (await rotations(p))[0]
    await rotation_result(p, first, unknown=True, status="FAILED")
    await no_batch(p)
    rows = await rotations(p)
    assert len(rows) == 2
    assert [row.request_json["target_face"] for row in rows] == ["B", "C"]
    await rotation_result(p, rows[1])
    await advance(p, batch=p.app[0])
    by_rack = {r.rack_id: r for r in await racks(p)}
    assert by_rack["OFFLINE-DRAIN"].arrival_evidence_id is None
    assert by_rack["OFFLINE-SECOND"].arrival_evidence_id is not None
    assert len(await rotations(p)) == 2


async def test_frozen_three_face_order_has_distinct_actions_and_no_departure(drainable):
    p = drainable
    await prepared(p, faces=("A", "C", "B"))
    await no_batch(p)
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    await no_batch(p)
    rows = await rotations(p)
    assert [row.request_json["target_face"] for row in rows] == ["C", "B"]
    assert rows[0].client_request_id != rows[1].client_request_id
    await rotation_result(p, rows[1])
    await advance(p, batch=p.app[0])
    await no_batch(p)
    assert len(await rotations(p)) == 2
    assert [r.request_payload["data"]["rack_face"] for r in await requests(p)] == ["A", "C", "B"]
    assert (await current(p)).archived_at is None


async def test_closed_face_history_can_be_removed_before_fresh_current_face_recovery(drainable):
    from automatic_picking.application.drain_rotation import DrainRotationFlow
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy import delete

    p = drainable
    await rotated(p)
    async with p.sessions.begin() as db:
        old = await db.scalar(
            select(AutomaticPickingReturnAllocation).where(AutomaticPickingReturnAllocation.rack_face == "A")
        )
        await db.execute(
            delete(AutomaticPickingReturnMotion).where(AutomaticPickingReturnMotion.archived_at.is_not(None))
        )
        await db.delete(old)
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == old.operation_id))
    fresh, scan = application(p)
    fresh.drain_flow = p.app[2]
    p.app[2].inbound.rotation = DrainRotationFlow(fresh.return_batch_flow, p.app[2].inbound.creator)
    p.app = (fresh, scan, p.app[2])
    p.faces = []
    await advance(p, batch=fresh)
    await advance(p, batch=fresh)
    assert [r.request_payload["data"]["rack_face"] for r in await requests(p)] == ["B"]
    assert len(await rotations(p)) == 1
    assert (await current(p)).returned_ids == []


async def test_current_face_can_return_success_to_original_root_after_rotation(drainable):
    from test_drain_return_postgresql import movement_result
    from test_return_allocation_postgresql import allocation

    p = drainable
    await rotated(p)
    await advance(p, batch=p.app[0])
    await batch_result(p, selected=2)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    a = await allocation(p)
    assert a.rack_face == "B" and a.drain_id == (await current(p)).id
    await movement_result(p, a)
    await advance(p, batch=p.app[0])
    root = await current(p)
    assert root.returned_ids == root.return_ids
    assert root.archived_at is None
    assert len(await rotations(p)) == 1


async def test_inactive_line_still_recovers_original_success_without_new_movement(drainable):
    from src.app.workline.models import WorkLine

    p = drainable
    await prepared(p)
    await no_batch(p)
    await rotation_result(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.is_active = False
    p.inbound_inputs = {}
    p.positions = {}
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is not None
    assert len(await rotations(p)) == 1
    assert len(await requests(p)) == 1


async def test_unknown_original_buffer_position_never_freezes_as_empty(drainable):
    p = drainable
    await prepared(p)
    p.positions = {}
    await advance(p, batch=p.app[0])
    await advance(p, batch=p.app[0])
    assert not await rotations(p)
    assert not await requests(p)
    assert (await racks(p))[0].arrival_evidence_id is not None


async def test_newer_pending_rack_action_blocks_only_this_rotation(drainable):
    from wes_plugin_sdk import TransportRackPosition

    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator

    p = drainable
    await prepared(p)
    await advance(p, batch=p.app[0])
    await batch_result(p, result="NO_BATCH")
    await processor(p, scan=p.app[1]).process_batch()
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
    rows = await rotations(p)
    assert len(rows) == 1
    assert (await racks(p))[0].arrival_evidence_id is not None
    assert len(await requests(p)) == 1


async def test_retained_old_fact_cannot_bypass_current_owner_after_closed_action_cleanup(drainable):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
    from sqlalchemy import delete

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportMember

    p = drainable
    await prepared(p, faces=("A", "C", "B"))
    await no_batch(p)
    old = (await rotations(p))[0]
    await rotation_result(p, old)
    await advance(p, batch=p.app[0])
    old_arrival = (await racks(p))[0].arrival_evidence_id
    await no_batch(p)
    latest = (await rotations(p))[-1]
    await rotation_result(p, latest)
    await advance(p, batch=p.app[0])
    async with p.sessions.begin() as db:
        await db.execute(
            delete(AutomaticPickingReturnMotion).where(AutomaticPickingReturnMotion.arrival_evidence_id == old_arrival)
        )
        allocation = await db.scalar(
            select(AutomaticPickingReturnAllocation).where(
                AutomaticPickingReturnAllocation.arrival_evidence_id == old_arrival
            )
        )
        await db.delete(allocation)
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == allocation.operation_id))
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == old.transport_task_id))
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == old.client_request_id)
        )
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == old.transport_task_id))
    p.faces = [(old_arrival, "OFFLINE-DRAIN", "C")]
    ordinary, _ = application(p)
    await advance(p, batch=ordinary)
    pending = [r for r in await requests(p) if r.status == "PENDING"]
    assert [r.request_payload["data"]["rack_face"] for r in pending] == ["B"]


async def test_unknown_ordinary_faces_keep_prior_closed_allocation_without_source_time(drainable):
    p = drainable
    await prepared(p, faces=("A", "C", "B"))

    class UnknownFaces:
        async def read_in_session(self, db, *, workline_id):
            return None

    p.app[0].return_batch_flow.face_reader = UnknownFaces()
    await no_batch(p)
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    await advance(p, batch=p.app[0])
    assert [r.request_payload["data"]["rack_face"] for r in await requests(p)] == ["A", "C"]
    assert len(await rotations(p)) == 1


async def test_independent_ready_before_installation_is_not_final_motion_evaluation(drainable):
    from test_drain_return_postgresql import movement_result
    from test_return_allocation_postgresql import allocation

    p = drainable
    await prepared(p)
    rotation = p.app[2].inbound.rotation
    p.app[2].inbound.rotation = None
    await advance(p, batch=p.app[0])
    await batch_result(p, selected=1)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    original = await allocation(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == original.transport_task_id)
        )
        task.status = "ACCEPTED"
    await advance(p, batch=p.app[0])
    p.app[2].inbound.rotation = rotation
    await advance(p, batch=p.app[0])
    await movement_result(p, original)
    await advance(p, batch=p.app[0])
    assert not await rotations(p)
    reqs = await requests(p)
    assert len(reqs) == 2
    assert reqs[1].request_payload["data"]["rack_face"] == "A"
    assert len(reqs[1].request_payload["data"]["return_candidates"]) == 1


async def test_new_fifo_head_outside_original_scope_does_not_keep_intermediate_face(drainable):
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from test_drain_progress_postgresql import additional_return
    from test_drain_return_postgresql import movement_result
    from test_return_allocation_postgresql import allocation

    p = drainable
    await prepared(p, faces=("A", "C", "B"))
    await advance(p, batch=p.app[0])
    await batch_result(p, selected=3)
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    await movement_result(p, await allocation(p))
    await advance(p, batch=p.app[0])
    root = await current(p)
    assert root.returned_ids == root.return_ids
    late = await additional_return(p, bin_code="A000000099", timestamp=9999)
    await rotation_result(p)
    await advance(p, batch=p.app[0])
    await advance(p, batch=p.app[0])
    assert [row.request_json["target_face"] for row in await rotations(p)] == ["C", "B"]
    assert len(await requests(p)) == 1
    assert late not in (await current(p)).return_ids
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, late)
        assert row.archived_at is None and row.allocation_id is None
