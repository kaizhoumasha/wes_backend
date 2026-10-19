"""9B：显式离线输入，真实 Transport/Evidence/事务，无设备派发。"""

from datetime import timedelta
from types import SimpleNamespace as N

import pytest
from sqlalchemy import select
from test_drain_progress_postgresql import (
    application,
    current,
    database,
    decided,
    drainable,
    observed,
    pending,
    qualified,
)
from test_return_allocation_postgresql import advance, arrived
from test_returns_postgresql import processor

from src.app.execution.models import TransportDecisionBinding
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def inbound_application(p):
    from automatic_picking.application.drain_inbound import DrainInboundFlow, MatchedDrainInbound
    from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator

    batch, scan, flow = application(p)
    window = RackInboundWindowService()

    class Events:
        async def publish_to(self, *args, **kwargs):
            return False

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

    class Reader:
        async def read_in_session(self, db, *, workline_id, operation_id, ready_evidence_id, rack_id):
            return p.inbound_inputs.get(rack_id)

    flow.inbound = DrainInboundFlow(ReliableRackTransportCreator(service, inbound_window=window), Reader())
    root = await current(p)
    p.inbound_inputs = {
        "OFFLINE-DRAIN": MatchedDrainInbound(
            workline_id=p.line.id,
            operation_id=root.operation_id,
            ready_evidence_id=root.response_evidence_id,
            rack_id="OFFLINE-DRAIN",
            target=TransportRackPosition("OFFLINE-DRAIN-WORK"),
            target_face="A",
            rcs_template_id=TransportRcsTemplateId.CTU01,
        )
    }
    p.inbound_service = service
    return batch, scan, flow


async def ready(p):
    batch, _, _ = application(p)
    await advance(p, batch=batch)
    await decided(p, "READY", publish=True)
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        db.add(
            WorkLinePosition(
                workline_id=p.line.id,
                workline_code=p.line.line_code,
                position_code="OFFLINE-DRAIN",
                position_type="RACK_POSITION",
                position_name="offline",
                position_role="SMT_RETURN_RACK_POSITION",
                allowed_rack_kind="RETURN",
                logic_location_code="OFFLINE-DRAIN-WORK",
                capacity=4,
                enabled=True,
            )
        )


async def tasks(p):
    async with p.sessions() as db:
        return list(
            (
                await db.scalars(
                    select(TransportTask).where(TransportTask.request_json["rcs_template_id"].as_string() == "CTU01")
                )
            ).all()
        )


async def test_published_ready_creates_one_workline_owned_ctu01_and_binding(drainable):
    p = drainable
    await ready(p)
    batch, _, _ = await inbound_application(p)
    await advance(p, batch=batch)
    rows = await tasks(p)
    assert rows[0].request_json["source"] == {"kind": "RACK", "location_code": "OFFLINE-DRAIN"}
    await advance(p, batch=batch)
    assert len(rows) == 1
    root = await current(p)
    async with p.sessions() as db:
        binding = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == rows[0].client_request_id
            )
        )
        assert binding.picking_task_id is None
        assert binding.source_evidence_id == root.response_evidence_id
        assert binding.correlation_id == f"drain:{root.operation_id}:OFFLINE-DRAIN"
        assert rows[0].request_json["source"] == {"kind": "RACK", "location_code": "OFFLINE-DRAIN"}
        assert rows[0].request_json["target_face"] == "A"


async def racks(p):
    from automatic_picking.application.drain_model import AutomaticPickingDrainRack

    async with p.sessions() as db:
        return list(
            (await db.scalars(select(AutomaticPickingDrainRack).order_by(AutomaticPickingDrainRack.rack_id))).all()
        )


async def result(
    p, task, *, status="SUCCEEDED", publish=True, process=True, unknown=False, location="OFFLINE-DRAIN-WORK"
):
    from src.core.uuid7 import new_uuid7

    payload = {
        "transport_task_id": task.transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": task.request_json["rack_id"],
        "status": status,
        "position_unknown": unknown,
    }
    if not unknown:
        payload.update(final_position={"kind": "RACK_POSITION", "location_code": location}, arrival_face="A")
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


async def publisher(p):
    from automatic_picking.application.plugin import build_plugin
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    batch, scan, _ = p.app
    return InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository()),),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )


async def started(p):
    await ready(p)
    p.app = await inbound_application(p)
    await advance(p, batch=p.app[0])
    return (await tasks(p))[0]


async def test_callback_before_ack_publishes_then_hands_off_without_input_or_executing(drainable):
    p = drainable
    task = await started(p)
    assert task.status == "PENDING"
    await result(p, task, process=False)
    assert (await racks(p))[0].arrival_evidence_id is None
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None
    await processor(p, scan=p.app[1]).process_batch()
    p.app = await inbound_application(p)
    p.inbound_inputs = {}
    await advance(p, batch=p.app[0])
    arrived_id = (await racks(p))[0].arrival_evidence_id
    assert arrived_id is not None
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id == arrived_id
    assert len(await tasks(p)) == 1


async def test_concurrent_recovery_creates_and_hands_off_once(drainable):
    import asyncio

    p = drainable
    await ready(p)
    p.app = await inbound_application(p)
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(4)))
    assert len(await tasks(p)) == len(await racks(p)) == 1
    await result(p, (await tasks(p))[0])
    await asyncio.gather(*(advance(p, batch=p.app[0]) for _ in range(4)))
    assert (await racks(p))[0].arrival_evidence_id is not None


@pytest.mark.parametrize("point", ["create", "associate", "arrival"])
async def test_crash_rolls_back_transport_association_and_arrival(drainable, point):
    from automatic_picking.application.drain_model import AutomaticPickingDrainRack
    from sqlalchemy import func

    from src.app.workline.models import WorkLine

    p = drainable
    await ready(p)
    p.app = await inbound_application(p)
    if point == "arrival":
        await advance(p, batch=p.app[0])
        await result(p, (await tasks(p))[0])
    flow = p.app[2].inbound
    if point == "create":
        original = flow.creator.create_windowed_inbound

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("offline crash")

        flow.creator.create_windowed_inbound = crash
        with pytest.raises(RuntimeError, match="offline crash"):
            await advance(p, batch=p.app[0])
    else:
        with pytest.raises(RuntimeError, match="offline crash"):
            async with p.sessions.begin() as db:
                line = await db.get(WorkLine, p.line.id, with_for_update=True)
                await p.app[0].advance_completed_in_session(db, line)
                assert await db.scalar(select(func.count()).select_from(AutomaticPickingDrainRack)) == 1
                raise RuntimeError("offline crash")
    if point != "arrival":
        assert not await tasks(p) and not await racks(p)
    else:
        assert (await racks(p))[0].arrival_evidence_id is None
    p.app = await inbound_application(p)
    await advance(p, batch=p.app[0])
    assert len(await tasks(p)) == len(await racks(p)) == 1
    assert ((await racks(p))[0].arrival_evidence_id is not None) == (point == "arrival")


@pytest.mark.parametrize("state", ["unpublished", "unknown", "wrong_location", "newer_projection"])
async def test_uncertain_or_stale_arrival_keeps_original_identity(drainable, state):
    from src.app.execution.models import PositionProjection

    p = drainable
    task = await started(p)
    await result(
        p,
        task,
        publish=state != "unpublished",
        unknown=state == "unknown",
        status="FAILED" if state == "unknown" else "SUCCEEDED",
        location="OTHER" if state == "wrong_location" else "OFFLINE-DRAIN-WORK",
    )
    if state == "newer_projection":
        async with p.sessions.begin() as db:
            projection = await db.scalar(
                select(PositionProjection).where(PositionProjection.object_id == "OFFLINE-DRAIN")
            )
            projection.source_causal_token += 100
    p.app = await inbound_application(p)
    p.inbound_inputs = {}
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None
    assert [t.transport_task_id for t in await tasks(p)] == [task.transport_task_id]


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", "old-operation"),
        ("ready_evidence_id", 999),
        ("workline_id", 999),
        ("rack_id", "OTHER"),
        ("target_face", "B"),
    ],
)
async def test_explicit_input_must_match_current_ready(drainable, field, value):
    from dataclasses import replace

    p = drainable
    await ready(p)
    p.app = await inbound_application(p)
    p.inbound_inputs["OFFLINE-DRAIN"] = replace(p.inbound_inputs["OFFLINE-DRAIN"], **{field: value})
    with pytest.raises(ValueError, match="exact current READY"):
        await advance(p, batch=p.app[0])
    assert not await tasks(p)


@pytest.mark.parametrize("state", ["unpublished", "not_applied", "missing_input", "inactive"])
async def test_no_new_transport_without_published_ready_and_explicit_input(drainable, state):
    from src.app.execution.models import InboundEvidence
    from src.app.workline.models import WorkLine

    p = drainable
    await ready(p)
    p.app = await inbound_application(p)
    async with p.sessions.begin() as db:
        if state in {"unpublished", "not_applied"}:
            fact = await db.get(InboundEvidence, (await current(p)).response_evidence_id)
            if state == "unpublished":
                fact.published_at = None
            else:
                fact.apply_status = "PENDING"
        if state == "inactive":
            (await db.get(WorkLine, p.line.id)).is_active = False
    if state == "missing_input":
        p.inbound_inputs = {}
    await advance(p, batch=p.app[0])
    assert not await tasks(p)


async def test_racks_progress_independently_of_ready_array_order(drainable):
    from dataclasses import replace

    from test_drain_progress_postgresql import additional_return

    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    p = drainable
    await additional_return(p)
    await ready(p)
    root = await current(p)
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, root.response_evidence_id)
        fact.normalized_payload = {
            **fact.normalized_payload,
            "data": {
                "result": "READY",
                "racks": [
                    {"rack_id": "WAITING-RACK", "rack_face": ["B"]},
                    {"rack_id": "OFFLINE-DRAIN", "rack_face": ["A"]},
                ],
            },
        }
        fact.payload_digest = canonical_json_digest(fact.normalized_payload)
    p.app = await inbound_application(p)
    await advance(p, batch=p.app[0])
    assert len(await tasks(p)) == 1
    await result(p, (await tasks(p))[0])
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is not None
    p.inbound_inputs["WAITING-RACK"] = replace(
        p.inbound_inputs["OFFLINE-DRAIN"], rack_id="WAITING-RACK", target_face="B"
    )
    await advance(p, batch=p.app[0])
    assert len(await tasks(p)) == 2
    assert [r.rack_id for r in await racks(p)] == ["OFFLINE-DRAIN", "WAITING-RACK"]


async def test_old_round_window_and_late_result_cannot_be_adopted(drainable):
    from automatic_picking.application.drain_model import AutomaticPickingDrain

    p = drainable
    old_task = await started(p)
    old = await current(p)
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingDrain, old.id)).archived_at = p.now
    batch, _, _ = application(p)
    await advance(p, batch=batch)
    await decided(p, "READY", publish=True)
    await advance(p, batch=batch)
    assert (await current(p)).id != old.id
    p.app = await inbound_application(p)
    await advance(p, batch=p.app[0])
    assert len(await tasks(p)) == len(await racks(p)) == 1
    assert (await racks(p))[0].drain_id == old.id
    await result(p, old_task)
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None
    assert len(await tasks(p)) == 1


async def test_current_handoff_survives_closed_history_cleanup_and_fresh_owner(drainable):
    from automatic_picking.application.feed_model import AutomaticPickingFeed, AutomaticPickingFeedScan
    from automatic_picking.application.work_model import AutomaticPickingWork
    from sqlalchemy import delete

    from src.app.execution.models import InboundEvidence, WmsConfirmation

    p = drainable
    batch, _, _ = application(p)
    await advance(p, batch=batch)
    old_id = await decided(p, publish=True)
    await advance(p, batch=batch)
    old = await current(p)
    p.now = old.retry_at
    await advance(p, batch=batch)
    async with p.sessions.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == old.operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == old_id))
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(AutomaticPickingFeedScan).where(AutomaticPickingFeedScan.feed_id == p.feed.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == p.feed.id))
    await decided(p, "READY", publish=True)
    await advance(p, batch=batch)
    # 当前请求已跨越已闭合前驱清理，此处不重新准备 READY。
    async with p.sessions.begin() as db:
        db.add(
            WorkLinePosition(
                workline_id=p.line.id,
                workline_code=p.line.line_code,
                position_code="DRAIN",
                position_name="offline",
                position_type="RACK_POSITION",
                position_role="SMT_RETURN_RACK_POSITION",
                allowed_rack_kind="RETURN",
                logic_location_code="OFFLINE-DRAIN-WORK",
                capacity=4,
                enabled=True,
            )
        )
    p.app = await inbound_application(p)
    await advance(p, batch=p.app[0])
    task = (await tasks(p))[0]
    await result(p, task)
    p.app = await inbound_application(p)
    p.inbound_inputs = {}
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is not None
    assert len(await tasks(p)) == 1


async def test_missing_consumer_defers_then_current_owner_recovers(drainable):
    from src.app.execution.models import InboundEvidence

    p = drainable
    task = await started(p)
    absent = application(p)
    p.app = absent
    await result(p, task)
    async with p.sessions() as db:
        evidence = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == task.transport_task_id)
        )
        assert evidence.published_at is None
    p.app = await inbound_application(p)
    # 到期重试仍由 FactProcessor 拥有；离线验收显式推进其截止时间。
    async with p.sessions.begin() as db:
        evidence = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == task.transport_task_id)
        )
        evidence.decision_next_attempt_at = None
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is not None


async def test_migration_roundtrip_schema_and_lossy_downgrade_guard(drainable, database):
    import subprocess

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.drain_model import AutomaticPickingDrainRack

    from tests.support.postgresql_heavy import run_alembic

    p = drainable
    url, _ = database
    run_alembic("downgrade", "a243886b4a58", database_url=url)
    run_alembic("upgrade", "head", database_url=url)

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare: (
                    kind != "table" or name == "automatic_picking_drain_racks"
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingDrainRack.metadata)

    async with p.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []
    await started(p)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "a243886b4a58", database_url=url)
    assert "cannot discard automatic drain rack execution identities" in error.value.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_request_id", "other"),
        ("source_evidence_id", 999),
        ("outcome_version", 0),
        ("status", "FAILED"),
        ("members", []),
        ("caller", {"workline_id": "999"}),
    ],
)
async def test_drifted_result_cannot_apply_or_handoff(drainable, field, value):
    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    p = drainable
    task = await started(p)
    await result(p, task)
    async with p.sessions.begin() as db:
        evidence = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == task.transport_task_id)
        )
        evidence.normalized_payload = {**evidence.normalized_payload, field: value}
        evidence.payload_digest = canonical_json_digest(evidence.normalized_payload)
        applied = await p.app[1].apply_in_session(db, evidence.id, workline_id=p.line.id)
        assert applied.disposition == "RECONCILING"
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None


@pytest.mark.parametrize("early_result", [False, True])
async def test_acceptance_is_not_arrival_and_late_ack_preserves_success(drainable, early_result):
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    p = drainable
    task = await started(p)

    class Provider:
        async def submit(self, **kwargs):
            if early_result:
                await result(p, task, publish=False)
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.inbound_service.provider = Provider()
    assert await p.inbound_service.submit_pending_tasks(1) == 1
    if early_result:
        assert await p.inbound_service.publish_pending_outcomes(100, await publisher(p)) == 1
        await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    row = (await racks(p))[0]
    assert (row.arrival_evidence_id is not None) == early_result
    assert len(await tasks(p)) == 1
    assert (await tasks(p))[0].status == ("SUCCEEDED" if early_result else "ACCEPTED")


async def test_fact_application_rollback_keeps_arrival_unpublished_until_recovered(drainable):
    from src.app.execution.models import InboundEvidence

    p = drainable
    task = await started(p)
    await result(p, task, process=False)
    async with p.sessions() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == task.transport_task_id)
        )
        fact_id = fact.id
    with pytest.raises(RuntimeError, match="offline application crash"):
        async with p.sessions.begin() as db:
            applied = await p.app[1].apply_in_session(db, fact_id, workline_id=p.line.id)
            assert applied.disposition == "APPLIED"
            raise RuntimeError("offline application crash")
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id is None
    await processor(p, scan=p.app[1]).process_batch()
    await advance(p, batch=p.app[0])
    assert (await racks(p))[0].arrival_evidence_id == fact_id
