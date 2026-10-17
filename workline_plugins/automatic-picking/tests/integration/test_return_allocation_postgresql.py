"""精确位置/架面和结果均由离线输入给定；不执行外部派发。"""

from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from sqlalchemy import select
from test_return_directions_postgresql import application as direction_application
from test_return_directions_postgresql import arrived, callback, database, observed, pending
from test_returns_postgresql import processor

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def qualified(observed):
    p = observed
    _, batch, scan = direction_application(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert await batch.advance_completed_in_session(db, line) == 1
    p.ready_id = await callback(p)
    assert await processor(p, scan=scan).process_batch() == 1
    async with p.sessions.begin() as db:
        fact = InboundEvidence(
            kind="DEVICE_EVENT",
            device_code="OFFLINE-FACE",
            source_identity="fixture-face:" + new_uuid7(),
            workline_id=p.line.id,
            normalized_payload={"fixture": "current rack face"},
            payload_digest=canonical_json_digest({"fixture": "current rack face"}),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now,
            decision_digest=canonical_json_digest({"fixture": "face"}),
        )
        db.add(fact)
        await db.flush()
        p.face_id = fact.id
    p.faces = [(p.face_id, "OFFLINE-RACK", "A")]
    p.positions = {p.return_id: "OFFLINE-OUTLET"}
    return p


class FaceReader:
    def __init__(self, p):
        self.p = p

    async def read_in_session(self, db, *, workline_id):
        from automatic_picking.application.return_allocation_flow import MatchedReturnFace

        return tuple(MatchedReturnFace(eid, workline_id, rack, face) for eid, rack, face in self.p.faces)


class LocationReader:
    def __init__(self, p):
        self.p = p

    async def read_in_session(self, db, *, return_id):
        from automatic_picking.application.return_allocation_flow import MatchedReturnLocation
        from automatic_picking.application.return_model import AutomaticPickingReturn

        if return_id not in self.p.positions:
            return None
        row = await db.get(AutomaticPickingReturn, return_id)
        return MatchedReturnLocation(return_id, row.workline_id, row.bin_code, self.p.positions[return_id])


def application(p, *, repository=None):
    from automatic_picking.application.return_allocation_flow import AutomaticPickingReturnBatchFlow
    from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository

    from src.app.execution.services.reliable_rack_transport import ReliableBinTransportCreator
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchScheduler
    from src.app.wms_integration.outbound_picking.services.return_batch_owner import ReturnBatchOwnerService

    transport = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    _, batch, scan = direction_application(p)
    batch.return_batch_flow = AutomaticPickingReturnBatchFlow(
        repository or ReturnAllocationRepository(),
        BinBatchScheduler(WmsConfirmationLifecycleService(workline_owner=ReturnBatchOwnerService())),
        ReliableBinTransportCreator(transport),
        FaceReader(p),
        LocationReader(p),
        clock=lambda: p.now,
    )
    scan.return_batch_flow = batch.return_batch_flow
    return batch, scan


async def advance(p, *, batch=None):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        return await (batch or application(p)[0]).advance_completed_in_session(db, line)


async def test_qualified_fifo_freezes_exact_request_and_current_member_atomically(qualified):
    p = qualified
    assert await advance(p) == 1
    assert await advance(p) == 0
    async with p.sessions() as db:
        request = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
        )
        assert request.request_payload["data"] == {
            "workline_code": p.line.line_code,
            "rack_id": "OFFLINE-RACK",
            "rack_face": "A",
            "return_candidates": [
                {
                    "sequence_no": 1,
                    "bin_code": "A123456789",
                    "source": {"type": "HANDOFF_POSITION", "location_code": "OFFLINE-OUTLET"},
                }
            ],
        }


async def allocation(p):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    async with p.sessions() as db:
        return await db.scalar(
            select(AutomaticPickingReturnAllocation).order_by(AutomaticPickingReturnAllocation.id.desc())
        )


async def result(p, *, selected=1, result="READY", **changes):
    a = await allocation(p)
    async with p.sessions.begin() as db:
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == a.operation_id))
        candidates = request.request_payload["data"]["return_candidates"]
        data = {
            "result": result,
            **(
                {
                    "moves": [
                        {
                            "sequence_no": c["sequence_no"],
                            "bin_code": c["bin_code"],
                            "target": {
                                "type": "RACK_BIN_SLOT",
                                "rack_id": a.rack_id,
                                "rack_face": a.rack_face,
                                "slot_id": "S" + str(c["sequence_no"]),
                            },
                        }
                        for c in candidates[:selected]
                    ]
                }
                if result == "READY"
                else {"retry_after_ms": 1000}
            ),
        }
        data.update(changes)
        payload = {"operation_id": a.operation_id, "code": "DECIDED", "timestamp": 2000, "data": data}
        evidence = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=request.operation + ":" + a.operation_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            workline_id=p.line.id,
            operation=request.operation,
            operation_id=a.operation_id,
            apply_status="APPLIED",
            received_at=p.now,
        )
        db.add(evidence)
        await db.flush()
        request.status, request.completed_at = "COMPLETED", p.now
        request.response_evidence_id = evidence.id
        request.response_result = result
        return evidence.id


async def test_ready_application_and_publication_freeze_exact_current_allocation(qualified):
    p = qualified
    assert await advance(p) == 1
    original = await allocation(p)
    eid = await result(p)
    await processor(p, scan=application(p)[1]).process_batch()
    current = await allocation(p)
    assert current.response_evidence_id == eid and current.selected_count == 1
    assert current.operation_id == original.operation_id and current.return_ids == [p.return_id]
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).published_at is not None


async def test_ready_creates_one_bin_move_and_accepted_handoff_is_atomic(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    from src.app.transport.models import TransportMember, TransportTask

    p = qualified
    assert await advance(p) == 1
    await result(p)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert await advance(p) == 1
    assert await advance(p) == 0
    a = await allocation(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
        assert task.kind == "BIN_MOVE" and task.status == "PENDING"
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
        )
        assert member.source_json == {"kind": "HANDOFF_POSITION", "location_code": "OFFLINE-OUTLET"}
        assert member.target_json == {
            "kind": "RACK_BIN_SLOT",
            "rack_id": "OFFLINE-RACK",
            "rack_face": "A",
            "slot_id": "S1",
        }
        original_body = task.submit_request_body
        task.status = "ACCEPTED"  # 离线给定权威接纳，不调用派发。
    assert await advance(p) == 1
    assert await advance(p) == 0
    a = await allocation(p)
    assert a.handed_off_at is not None and a.settled_at is None
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.archived_at is not None and row.allocation_id is None
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
        assert task.submit_request_body == original_body
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
        )
        assert member.final_position_json is None  # 接纳和交接不伪造物理到位。


async def add_qualified(p, *, bin_code="A000000002", timestamp=2000):
    from test_return_fifo_postgresql import other_return, scan4

    return_id = await other_return(p, bin_code=bin_code)
    await scan4(p, return_id=return_id, timestamp=timestamp)
    assert await processor(p, scan=direction_application(p)[2]).process_batch() == 1
    _, batch, scan = direction_application(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert await batch.advance_completed_in_session(db, line) == 1
    original_id = p.return_id
    p.return_id = return_id
    try:
        eid = await callback(p)
        assert await processor(p, scan=scan).process_batch() == 1
    finally:
        p.return_id = original_id
    p.positions[return_id] = "OFFLINE-OUTLET"
    return return_id, eid


async def new_face(p, rack="OFFLINE-RACK", face="A"):
    async with p.sessions.begin() as db:
        payload = {"fixture": "new authoritative face", "round": new_uuid7()}
        evidence = InboundEvidence(
            kind="DEVICE_EVENT",
            device_code="OFFLINE-FACE",
            source_identity="fixture-face:" + new_uuid7(),
            workline_id=p.line.id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now,
            decision_digest=canonical_json_digest(payload),
        )
        db.add(evidence)
        await db.flush()
        return evidence.id, rack, face


async def complete_transport(p, *, unknown=False, publish=True):
    from dataclasses import asdict

    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.transport.contracts import RackBinSlot, TransportCaller, TransportMemberOutcome, TransportOutcome
    from src.app.transport.models import TransportMember, TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    a = await allocation(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
        members = list(
            (
                await db.scalars(
                    select(TransportMember)
                    .where(TransportMember.transport_task_id == task.transport_task_id)
                    .order_by(TransportMember.ordinal)
                )
            ).all()
        )
        facts = []
        for member in members:
            target = member.target_json
            member.status = "FAILED" if unknown else "SUCCEEDED"
            member.position_unknown = unknown
            member.final_position_json = None if unknown else target
            position = None if unknown else RackBinSlot(target["rack_id"], target["rack_face"], target["slot_id"])
            facts.append(TransportMemberOutcome(member.object_id, final_position=position, position_unknown=unknown))
        task.status = "RECONCILING" if unknown else "SUCCEEDED"
        task.outcome_version += 1
        outcome = TransportOutcome(
            task.transport_task_id,
            task.client_request_id,
            task.outcome_version,
            TransportCaller(str(p.line.id)),
            "UNKNOWN" if unknown else "SUCCEEDED",
            None,
            tuple(facts),
        )
        task.outcome_json = asdict(outcome)
    if publish:
        batch, scan = application(p)
        plugin = build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository())
        publisher = InstalledPluginTransportOutcomePublisher(
            p.sessions,
            (plugin,),
            queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
        )
        service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
        assert await service.publish_pending_outcomes(100, publisher) == 1
    return outcome


async def requested_ready(p, *, selected=1):
    assert await advance(p) == 1
    eid = await result(p, selected=selected)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    return eid


async def created(p, *, selected=1):
    eid = await requested_ready(p, selected=selected)
    assert await advance(p) == 1
    return eid


async def accept(p):
    from src.app.transport.models import TransportTask

    a = await allocation(p)
    async with p.sessions.begin() as db:
        (
            await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
        ).status = "ACCEPTED"
    assert await advance(p) == 1


async def test_fifo_never_skips_unqualified_or_unknown_physical_head(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified
    await add_qualified(p)
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id = None
    assert await advance(p) == 0
    assert await allocation(p) is None


async def test_prefix_is_bounded_to_four_and_stops_at_first_missing_position(qualified):
    p = qualified
    ids = [p.return_id]
    for n in range(2, 7):
        rid, _ = await add_qualified(p, bin_code=f"A00000000{n}", timestamp=n * 1000)
        ids.append(rid)
    assert await advance(p) == 1
    a = await allocation(p)
    assert a.return_ids == ids[:4]
    async with p.sessions() as db:
        c = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == a.operation_id))
        assert [v["sequence_no"] for v in c.request_payload["data"]["return_candidates"]] == [1, 2, 3, 4]


async def test_partial_ready_releases_only_unselected_prefix_tail(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified
    second, _ = await add_qualified(p)
    assert await advance(p) == 1
    await result(p, selected=1)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    a = await allocation(p)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).allocation_id == a.id
        assert (await db.get(AutomaticPickingReturn, second)).allocation_id is None
    assert await advance(p) == 1
    assert await advance(p) == 0  # 原搬运尚未取得已发布、已知终位的成功结果。


@pytest.mark.parametrize("total,selected", [(2, 1), (6, 4)])
async def test_same_arrival_ready_tail_continues_only_after_published_success(qualified, total, selected):
    import asyncio

    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    p = qualified
    ids = [p.return_id]
    for n in range(2, total + 1):
        rid, _ = await add_qualified(p, bin_code=f"A{n:09d}", timestamp=n * 1000)
        ids.append(rid)
    await created(p, selected=selected)
    original = await allocation(p)
    await accept(p)
    assert await advance(p) == 0
    await complete_transport(p)
    assert await advance(p) == 0  # 宿主已发布，插件事实尚未应用发布。
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 2]
    following = await allocation(p)
    assert following.arrival_evidence_id == original.arrival_evidence_id
    assert following.operation_id != original.operation_id
    assert following.return_ids == ids[selected:]
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnAllocation, original.id)).archived_at is not None
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == following.operation_id))
        assert [c["sequence_no"] for c in request.request_payload["data"]["return_candidates"]] == list(
            range(1, total - selected + 1)
        )
    async with p.sessions.begin() as db:
        await db.delete(await db.get(AutomaticPickingReturnAllocation, original.id))
    assert await advance(p) == 0


async def test_no_batch_closes_this_arrival_and_new_candidate_does_not_reopen_it(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified
    assert await advance(p) == 1
    eid = await result(p, result="NO_BATCH")
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert (await allocation(p)).settled_at is not None
    await add_qualified(p)
    p.now += timedelta(seconds=2)
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).allocation_id is None
        assert (await db.get(InboundEvidence, eid)).published_at is not None
        assert (
            len(
                list(
                    (
                        await db.scalars(
                            select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
                        )
                    ).all()
                )
            )
            == 1
        )
    p.faces = [await new_face(p)]
    assert await advance(p) == 1


async def test_frozen_request_unknown_preserves_identity_and_new_bins_are_not_appended(qualified):
    p = qualified
    assert await advance(p) == 1
    original = await allocation(p)
    async with p.sessions() as db:
        before = (
            await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == original.operation_id))
        ).request_payload
    await add_qualified(p)
    assert await advance(p) == 0
    async with p.sessions() as db:
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == original.operation_id))
        assert request.request_payload == before and request.status == "PENDING"
    assert (await allocation(p)).return_ids == [p.return_id]


async def test_same_face_waits_after_accepted_and_independent_face_can_request(qualified):
    p = qualified
    await created(p)
    await accept(p)
    await add_qualified(p)
    p.faces = [await new_face(p)]
    assert await advance(p) == 0
    p.faces.append(await new_face(p, rack="INDEPENDENT", face="B"))
    assert await advance(p) == 1
    assert (await allocation(p)).rack_id == "INDEPENDENT"


async def test_same_face_requires_host_and_business_publication_and_known_target(qualified):
    p = qualified
    await created(p)
    await accept(p)
    first_allocation = await allocation(p)
    await add_qualified(p)
    p.faces = [await new_face(p)]
    await complete_transport(p, publish=False)
    assert await advance(p) == 0
    # 权威终态已给定，再沿原 Transport 的真实发布链恢复。
    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.transport.models import TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    batch, scan = application(p)
    plugin = build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository())
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (plugin,),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await advance(p) == 0
    assert await processor(p, scan=scan).process_batch() == 1
    assert await advance(p) == 2  # 原分配收口，再为新的实际到位创建一次请求。
    assert (await allocation(p)).id != first_allocation.id


async def test_accepted_handoff_and_same_face_gate_survive_closed_history_cleanup(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from automatic_picking.application.work_model import AutomaticPickingWork
    from sqlalchemy import delete

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = qualified
    await created(p)
    await accept(p)
    a = await allocation(p)
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingReturn).where(AutomaticPickingReturn.id == p.return_id))
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == p.feed.id))
        await db.execute(delete(WmsConfirmation))
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    p.faces = []
    assert await advance(p) == 0
    await complete_transport(p)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert await advance(p) == 1
    a = await allocation(p)
    assert a.handed_off_at is not None and a.settled_at is not None


@pytest.mark.parametrize("point", ["claim", "confirmation"])
async def test_request_and_claim_crash_roll_back_as_one_transaction(qualified, point):
    from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified

    class Crash(ReturnAllocationRepository):
        async def add(self, *args, **kwargs):
            value = await super().add(*args, **kwargs)
            if point == "claim":
                raise RuntimeError("fixture request crash")
            return value

    batch, _ = application(p, repository=Crash())
    original = batch.return_batch_flow.scheduler

    class CrashConfirmation:
        async def create_in_session(self, *args, **kwargs):
            await original.create_in_session(*args, **kwargs)
            if point == "confirmation":
                raise RuntimeError("fixture request crash")

    batch.return_batch_flow.scheduler = CrashConfirmation()
    with pytest.raises(RuntimeError, match="fixture request crash"):
        await advance(p, batch=batch)
    assert await allocation(p) is None
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).allocation_id is None
        assert (
            await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1"))
            is None
        )
    assert await advance(p) == 1


@pytest.mark.parametrize("point", ["decision", "publication"])
async def test_no_batch_release_result_and_publication_are_atomic(qualified, point):
    from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository
    from automatic_picking.application.return_model import AutomaticPickingReturn

    from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository

    p = qualified
    assert await advance(p) == 1
    eid = await result(p, result="NO_BATCH")

    class Crash(ReturnAllocationRepository):
        async def apply_decision(self, *args, **kwargs):
            await super().apply_decision(*args, **kwargs)
            if point == "decision":
                raise RuntimeError("fixture decision crash")

    class CrashPublication(InboundEvidenceRepository):
        async def flush(self, db):
            if point == "publication" and (await db.get(InboundEvidence, eid)).published_at is not None:
                raise RuntimeError("fixture publication crash")
            await super().flush(db)

    _, scan = application(p, repository=Crash())
    assert await processor(p, scan=scan, evidences=CrashPublication()).process_batch() == 0
    a = await allocation(p)
    assert a.response_evidence_id is None and a.settled_at is None
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).allocation_id == a.id
        assert (await db.get(InboundEvidence, eid)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert (await allocation(p)).settled_at is not None


@pytest.mark.parametrize("point", ["transport", "handoff"])
async def test_transport_binding_and_accepted_handoff_rollback(qualified, point):
    from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository
    from automatic_picking.application.return_model import AutomaticPickingReturn

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportTask

    p = qualified
    await requested_ready(p)

    class Crash(ReturnAllocationRepository):
        async def bind_transport(self, *args, **kwargs):
            await super().bind_transport(*args, **kwargs)
            if point == "transport":
                raise RuntimeError("fixture transport crash")

        async def handoff(self, *args, **kwargs):
            await super().handoff(*args, **kwargs)
            if point == "handoff":
                raise RuntimeError("fixture transport crash")

    if point == "handoff":
        assert await advance(p) == 1
        a = await allocation(p)
        async with p.sessions.begin() as db:
            (
                await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == a.transport_task_id))
            ).status = "ACCEPTED"
    with pytest.raises(RuntimeError, match="fixture transport crash"):
        await advance(p, batch=application(p, repository=Crash())[0])
    a = await allocation(p)
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.archived_at is None and row.allocation_id == a.id
        assert a.handed_off_at is None
        if point == "transport":
            assert a.transport_task_id is None
            assert (
                await db.scalar(
                    select(TransportDecisionBinding).where(TransportDecisionBinding.step == "AUTOMATIC_PICKING_RETURN")
                )
                is None
            )
    assert await advance(p) == 1


async def test_concurrent_requests_results_and_transport_creation_are_unique(qualified):
    import asyncio

    from sqlalchemy import func

    from src.app.transport.models import TransportTask

    p = qualified
    assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 1]
    eid = await result(p)
    await asyncio.gather(
        processor(p, scan=application(p)[1]).process_batch(), processor(p, scan=application(p)[1]).process_batch()
    )
    assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 1]
    a = await allocation(p)
    async with p.sessions() as db:
        assert a.response_evidence_id == eid
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 1
    await accept(p)
    assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 0]


async def test_unpublished_ready_and_source_unknown_cannot_create_transport(qualified):
    p = qualified
    eid = await requested_ready(p)
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, eid)).published_at = None
    assert await advance(p) == 0
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    p.positions.clear()
    assert await advance(p) == 0
    p.positions[p.return_id] = "OFFLINE-OUTLET"
    assert await advance(p) == 1


async def test_missing_middle_position_truncates_prefix_without_selecting_later_bin(qualified):
    p = qualified
    middle, _ = await add_qualified(p)
    _later, _ = await add_qualified(p, bin_code="A000000003", timestamp=3000)
    del p.positions[middle]
    assert await advance(p) == 1
    assert (await allocation(p)).return_ids == [p.return_id]


async def test_old_no_batch_replay_cannot_release_new_arrival_claim(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified
    assert await advance(p) == 1
    eid = await result(p, result="NO_BATCH")
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    old = await allocation(p)
    p.faces = [await new_face(p)]
    assert await advance(p) == 1
    new = await allocation(p)
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, eid)).published_at = None
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).allocation_id == new.id
    assert new.id != old.id


async def test_lease_expiry_restores_original_result_without_notification(qualified):
    from src.app.execution.repositories import inbound_evidence_repository

    p = qualified
    assert await advance(p) == 1
    eid = await result(p)
    async with p.sessions.begin() as db:
        rows = await inbound_evidence_repository.claim_decision_batch(
            db,
            now=p.now,
            claim_token="dead-return-allocation",
            claim_expires_at=p.now + timedelta(seconds=30),
            limit=100,
            business_wms_routes=((p.line.plugin_key, p.line.plugin_version, "outbound.bin.return_batch@v1"),),
        )
        assert [row.id for row in rows] == [eid]
    assert await processor(p, scan=application(p)[1]).process_batch() == 0
    p.now += timedelta(seconds=31)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert (await allocation(p)).response_evidence_id == eid


async def test_unknown_terminal_position_keeps_same_face_gate(qualified):
    p = qualified
    await created(p)
    await accept(p)
    await complete_transport(p, unknown=True)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    await add_qualified(p)
    p.faces = [await new_face(p)]
    assert await advance(p) == 0
    assert (await allocation(p)).settled_at is None


async def test_migration_preserves_existing_returns_and_refuses_allocation_loss(qualified, database):
    import subprocess

    from automatic_picking.application.return_model import AutomaticPickingReturn

    from tests.support.postgresql_heavy import run_alembic

    p = qualified
    url, _ = database
    run_alembic("downgrade", "71246b611dbf", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.allocation_id is None and row.ready_evidence_id == p.ready_id
    assert await advance(p) == 1
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "71246b611dbf", database_url=url)
    assert "cannot discard automatic return allocation identities or obligations" in error.value.stderr


async def test_allocation_model_matches_migration(qualified):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare: (
                    kind != "table" or name in {"automatic_picking_return_allocations", "automatic_picking_returns"}
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingReturnAllocation.metadata)

    async with qualified.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []


async def test_completed_old_service_progress_can_be_cleaned_without_affecting_new_round(qualified):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation
    from sqlalchemy import delete

    p = qualified
    assert await advance(p) == 1
    old = await allocation(p)
    old_eid = await result(p, result="NO_BATCH")
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    p.faces = [await new_face(p)]
    assert await advance(p) == 1
    async with p.sessions.begin() as db:
        assert (await db.get(AutomaticPickingReturnAllocation, old.id)).archived_at is not None
        await db.execute(delete(AutomaticPickingReturnAllocation).where(AutomaticPickingReturnAllocation.id == old.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == old.operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == old_eid))
    eid = await result(p)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert await advance(p) == 1
    assert (await allocation(p)).response_evidence_id == eid


async def test_active_frozen_target_conflict_keeps_original_ready_without_creating_move(qualified):
    from sqlalchemy import func

    from src.app.execution.services.reliable_rack_transport import ReliableBinTransportCreator
    from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot
    from src.app.transport.models import TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService

    p = qualified
    eid = await requested_ready(p)
    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    async with p.sessions.begin() as db:
        await ReliableBinTransportCreator(service).create(
            db,
            workline_id=p.line.id,
            source_evidence_id=p.ready_id,
            correlation_id="fixture-other:" + new_uuid7(),
            step="FIXTURE_ONLY",
            resource_fence_id=new_uuid7(),
            moves=(BinMove("OTHER-BIN", HandoffPosition("OFFLINE-OTHER"), RackBinSlot("OFFLINE-RACK", "A", "S1")),),
        )
    assert await advance(p) == 0
    a = await allocation(p)
    assert a.response_evidence_id == eid and a.transport_task_id is None
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 1


async def test_nonprefix_wms_result_is_not_applied_or_used_for_transport(qualified):
    from automatic_picking.application.return_model import AutomaticPickingReturn

    p = qualified
    await add_qualified(p)
    assert await advance(p) == 1
    eid = await result(
        p,
        moves=[
            {
                "sequence_no": 2,
                "bin_code": "A000000002",
                "target": {"type": "RACK_BIN_SLOT", "rack_id": "OFFLINE-RACK", "rack_face": "A", "slot_id": "S2"},
            }
        ],
    )
    await processor(p, scan=application(p)[1]).process_batch()
    a = await allocation(p)
    assert a.response_evidence_id is None and a.transport_task_id is None
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).apply_status == "RECONCILING"
        assert (await db.get(AutomaticPickingReturn, p.return_id)).allocation_id == a.id
    assert await advance(p) == 0


async def test_late_ready_for_old_face_cannot_create_move_at_new_face(qualified):
    p = qualified
    assert await advance(p) == 1
    old = await allocation(p)
    p.faces = [await new_face(p, rack="OTHER-RACK", face="B")]
    eid = await result(p)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert await advance(p) == 0
    a = await allocation(p)
    assert a.id == old.id and a.transport_task_id is None and a.response_evidence_id == eid


async def test_older_transport_outcome_replay_does_not_overwrite_newer_current_result(qualified):
    p = qualified
    await created(p)
    await accept(p)
    await complete_transport(p, unknown=True)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    first = (await allocation(p)).transport_result_evidence_id
    await complete_transport(p)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    second = (await allocation(p)).transport_result_evidence_id
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, first)).published_at = None
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert (await allocation(p)).transport_result_evidence_id == second != first
    assert await advance(p) == 1


async def test_unknown_current_face_preserves_closed_round_marker_without_requery(qualified):
    p = qualified
    assert await advance(p) == 1
    await result(p, result="NO_BATCH")
    assert await processor(p, scan=application(p)[1]).process_batch() == 1

    class UnknownFace:
        async def read_in_session(self, db, *, workline_id):
            return None

    batch, _ = application(p)
    batch.return_batch_flow.face_reader = UnknownFace()
    assert await advance(p, batch=batch) == 0
    assert (await allocation(p)).archived_at is None
    assert await advance(p) == 0


async def test_current_allocation_blocks_switch_after_return_closed_until_terminal(qualified):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.work_model import AutomaticPickingWork
    from automatic_picking.application.work_repository import WorkRepository
    from sqlalchemy import delete

    p = qualified
    await created(p)
    await accept(p)
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork))
        await db.execute(delete(AutomaticPickingFeed))
        summary = await WorkRepository().get_unfinished_workload_summary(db, p.line.id)
        assert summary["count"] == 1
    await complete_transport(p)
    assert await processor(p, scan=application(p)[1]).process_batch() == 1
    assert await advance(p) == 1
    async with p.sessions() as db:
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == 0


async def test_unpublished_independent_face_does_not_block_known_face_request(qualified):
    p = qualified
    await created(p)
    await accept(p)
    await add_qualified(p)
    unknown_face = await new_face(p)
    known_face = await new_face(p, rack="INDEPENDENT", face="B")
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, unknown_face[0])).published_at = None
    p.faces = [unknown_face, known_face]
    assert await advance(p) == 1
    assert (await allocation(p)).rack_id == "INDEPENDENT"
