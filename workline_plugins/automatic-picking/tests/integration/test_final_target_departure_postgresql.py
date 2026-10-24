"""任务完成后的实际转运架离场；复用既有搬运/单盘证据，只验收本段持久接续。"""

from datetime import timedelta
from types import SimpleNamespace as N

import pytest
from sqlalchemy import select
from test_normal_picking_postgresql import tick
from test_pickup_identity_postgresql import database, prepared
from test_return_rack_completion_postgresql import (
    COMPLETION,
    DEPARTURE,
    answer,
    callback,
    completed_member,
    completion_tick,
    confirmation,
    offline_wakeups,
)
from test_return_rack_completion_postgresql import (
    prepared as prepared_task,
)
from test_target_replacement_departure_postgresql import replacing
from test_target_replacement_inbound_postgresql import incoming
from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

from src.app.execution.models import TransportDecisionBinding
from src.app.transport.models import TransportTask
from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def initial_target(p):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.position_bindings = {**line.position_bindings, "TRANSFER_RACK": {"location_id": "OFFLINE-TRANSFER"}}
        h = await p.plugin.picking_task_batch_driver.return_rack_flow.creator.create(
            db,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.task.issued_evidence_id,
            correlation_id="offline-final-target",
            step="PICKING_TASK_TARGET_RACK_IN",
            resource_fence_id="TARGET",
            intent=N(
                rack_id="TARGET",
                source=TransportRackPosition("OFFLINE-STORAGE"),
                target=TransportRackPosition("OFFLINE-TRANSFER"),
                target_face="A",
                rcs_template_id=TransportRcsTemplateId.F01,
            ),
        )
        root = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == h.transport_task_id))
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == h.client_request_id)
        )
        binding.window_target_location_code = "OFFLINE-TRANSFER"
    await callback(p, root)
    p.now += timedelta(seconds=1)  # 显式测试时钟晚于已接收的到位事实。
    return root


async def complete(p):
    await completed_member(p)
    assert await completion_tick(p) == 1
    await answer(p, COMPLETION, {"result": "COMPLETED"}, publish=False)
    assert await completion_tick(p) == 1


async def out(p):
    async with p.sessions() as db:
        return await db.scalar(
            select(TransportTask)
            .join(
                TransportDecisionBinding, TransportDecisionBinding.client_request_id == TransportTask.client_request_id
            )
            .where(TransportDecisionBinding.step == "AUTOMATIC_PICKING_FINAL_TARGET_OUT")
        )


async def test_completed_task_final_target_wait_ready_and_result_are_one_recoverable_flow(database):
    p = await prepared_task(database)
    root = await initial_target(p)
    await tick(p, p.plugin)
    assert await confirmation(p, DEPARTURE) is None
    await complete(p)
    await tick(p, p.plugin)
    first = await confirmation(p, DEPARTURE)
    assert first is not None, "completed task must create its final target departure request"
    assert first.picking_task_id == p.task.id and first.workline_id is None
    assert first.request_payload["data"]["task_id"] == p.task.task_id
    assert first.request_payload["data"]["rack_id"] == "TARGET"
    await answer(p, DEPARTURE, {"result": "WAIT", "retry_after_ms": 1000}, publish=False)
    p.now += timedelta(milliseconds=999)
    await tick(p, p.plugin)
    assert (await confirmation(p, DEPARTURE)).id == first.id
    p.now += timedelta(milliseconds=1)
    await tick(p, p.plugin)
    second = await confirmation(p, DEPARTURE)
    assert second.id != first.id
    await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-RETURN"}},
        publish=False,
    )
    await tick(p, p.plugin)
    departure = await out(p)
    assert departure is not None and departure.request_json["rcs_template_id"] == "F01"
    assert departure.request_json["rack_id"] == "TARGET"
    await tick(p, p.plugin)
    assert (await out(p)).id == departure.id
    await callback(p, departure)
    await tick(p, p.plugin)
    from automatic_picking.application.final_target_model import AutomaticPickingFinalTargetRack

    async with p.sessions() as db:
        current = await db.scalar(select(AutomaticPickingFinalTargetRack))
        assert current.root_transport_task_id == root.transport_task_id
        assert current.departure_transport_task_id == departure.transport_task_id
        assert current.closed_evidence_id is not None


async def ready_departure(database):
    p = await prepared_task(database)
    p.root = await initial_target(p)
    await complete(p)
    await tick(p, p.plugin)
    await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-RETURN"}},
        publish=False,
    )
    return p


async def test_concurrent_ready_creation_and_crash_roll_back_action_and_reference_together(database):
    import asyncio

    from automatic_picking.application.final_target_model import AutomaticPickingFinalTargetRack
    from sqlalchemy import func

    p = await ready_departure(database)
    flow = p.plugin.picking_task_batch_driver.final_target_flow
    original = flow.creator

    class CrashAfterCreate:
        async def create_transfer_departure(self, *args, **kwargs):
            await original.create_transfer_departure(*args, **kwargs)
            raise RuntimeError("offline transaction crash")

    flow.creator = CrashAfterCreate()
    with pytest.raises(RuntimeError, match="offline transaction crash"):
        await tick(p, p.plugin)
    assert await out(p) is None
    async with p.sessions() as db:
        assert (await db.scalar(select(AutomaticPickingFinalTargetRack))).departure_transport_task_id is None
    flow.creator = original
    await asyncio.gather(*(tick(p, p.plugin) for _ in range(3)))
    departure = await out(p)
    assert departure is not None
    async with p.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TransportDecisionBinding)
                .where(TransportDecisionBinding.step == "AUTOMATIC_PICKING_FINAL_TARGET_OUT")
            )
            == 1
        )


async def test_current_wait_restart_old_result_and_unpublished_original_out_do_not_close(database):
    from automatic_picking.application.final_target_model import AutomaticPickingFinalTargetRack
    from test_direct_pick_progress_postgresql import application
    from test_normal_picking_postgresql import processor

    from src.app.execution.models import InboundEvidence
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    p = await prepared_task(database)
    await initial_target(p)
    await complete(p)
    await tick(p, p.plugin)
    first = await confirmation(p, DEPARTURE)
    wait_id = await answer(p, DEPARTURE, {"result": "WAIT", "retry_after_ms": 1000}, publish=False)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        deadline = (await db.scalar(select(AutomaticPickingFinalTargetRack))).departure_retry_at
        assert deadline == p.now + timedelta(seconds=1)
    p.plugin = await application(p)
    p.now += timedelta(seconds=1)
    await tick(p, p.plugin)
    current = await confirmation(p, DEPARTURE)
    assert current.id != first.id
    # 重放旧 WAIT 留在旧 Evidence，不得回写当前游标或生成第三次请求。
    async with p.sessions() as db:
        old = await p.plugin.picking_task_batch_driver.final_target_flow.repository.reader.by_id(
            db, picking_task_id=p.task.id, confirmation_id=first.id
        )
        assert old.evidence_id == wait_id
    await tick(p, p.plugin)
    assert (await confirmation(p, DEPARTURE)).id == current.id
    await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-RETURN"}},
        publish=False,
    )
    await tick(p, p.plugin)
    departure = await out(p)
    await callback(p, departure, publish=False)  # 回调早于提交 ACK。
    await tick(p, p.plugin)
    async with p.sessions() as db:
        assert (await db.scalar(select(AutomaticPickingFinalTargetRack))).closed_evidence_id is None
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (p.plugin,),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    await p.service.publish_pending_outcomes(100, publisher)
    await processor(p, p.plugin).process_batch()
    await tick(p, p.plugin)
    p.plugin = await application(p)
    await tick(p, p.plugin)
    assert (await out(p)).id == departure.id
    async with p.sessions() as db:
        assert (await db.scalar(select(AutomaticPickingFinalTargetRack))).closed_evidence_id is not None


async def test_accepted_out_survives_root_cleanup_and_new_independent_task(database):
    from automatic_picking.application.final_target_model import AutomaticPickingFinalTargetRack
    from sqlalchemy import delete
    from test_direct_pick_progress_postgresql import application

    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.app.transport.models import TransportMember
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = await ready_departure(database)
    await tick(p, p.plugin)
    departure = await out(p)

    class OfflineProvider:
        async def submit(self, **kwargs):
            assert kwargs["transport_task_id"] == departure.transport_task_id
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.service.provider = OfflineProvider()
    assert await p.service.submit_pending_tasks(1) == 1
    async with p.sessions.begin() as db:
        window = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == p.root.client_request_id
            )
        )
        assert window.window_released_at is not None
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == p.root.transport_task_id))
        await db.delete(window)
        await db.delete(await db.get(TransportTask, p.root.id))
        from src.app.execution.models import InboundEvidence
        from src.utils.canonical_json import canonical_json_digest

        next_fact = InboundEvidence(
            kind="WMS_EVENT",
            operation="outbound.picking_task.issued@v1",
            operation_id="019f3405-2200-7b01-8b01-000000000099",
            source_identity="fixture-next-task",
            normalized_payload={},
            payload_digest=canonical_json_digest({}),
            received_at=p.now,
            processed_at=p.now,
            apply_status="APPLIED",
        )
        db.add(next_fact)
        await db.flush()
        new_task = PickingTask(
            **{
                **p.task.model_dump(exclude={"id", "version", "created_at", "updated_at"}),
                "task_id": "NEXT-INDEPENDENT",
                "status": "EXECUTING",
                "issued_evidence_id": next_fact.id,
            }
        )
        db.add(new_task)
    p.plugin = await application(p)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        assert (await db.scalar(select(AutomaticPickingFinalTargetRack))).closed_evidence_id is None
        assert (await db.get(PickingTask, p.task.id)).status == "EXECUTION_COMPLETED"
    await callback(p, departure)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        assert (await db.scalar(select(AutomaticPickingFinalTargetRack))).closed_evidence_id is not None
        assert (
            await db.scalar(select(PickingTask).where(PickingTask.task_id == "NEXT-INDEPENDENT"))
        ).status == "EXECUTING"


@pytest.mark.parametrize("case", ["unknown", "changed_face", "wrong_owner", "same_destination"])
async def test_ready_never_uses_wrong_current_arrival_or_destination(database, case):
    from src.app.execution.models import InboundEvidence, PositionProjection, WmsConfirmation
    from src.utils.canonical_json import canonical_json_digest

    p = await ready_departure(database)
    async with p.sessions.begin() as db:
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "TARGET"))
        if case == "unknown":
            position.position_unknown = True
        elif case == "changed_face":
            position.arrival_face = "B"
        elif case == "wrong_owner":
            conf = await db.get(WmsConfirmation, (await confirmation(p, DEPARTURE)).id)
            conf.workline_id, conf.picking_task_id = p.line.id, None
        else:
            conf = await db.get(WmsConfirmation, (await confirmation(p, DEPARTURE)).id)
            evidence = await db.get(InboundEvidence, conf.response_evidence_id)
            evidence.normalized_payload = {
                **evidence.normalized_payload,
                "data": {
                    "result": "READY",
                    "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-TRANSFER"},
                },
            }
            evidence.payload_digest = canonical_json_digest(evidence.normalized_payload)
    if case in {"wrong_owner", "same_destination"}:
        with pytest.raises(ValueError):
            await tick(p, p.plugin)
    else:
        await tick(p, p.plugin)
    assert await out(p) is None


@pytest.mark.parametrize("replacing", ["no_next_source"], indirect=True)
async def test_replacement_actual_target_closes_then_departs_for_original_completed_task(incoming):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.final_target_model import AutomaticPickingFinalTargetRack
    from test_material_close_postgresql import report_result
    from test_normal_picking_postgresql import application, processor
    from test_placement_report_postgresql import successful_put
    from test_rotated_target_put_postgresql import removed
    from test_target_put_postgresql import advance
    from test_target_replacement_departure_postgresql import application as target_application
    from test_target_replacement_departure_postgresql import callback as target_callback
    from test_target_replacement_inbound_postgresql import inbound

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = incoming
    assert await advance(p, target_application(p)) == 1
    replacement = await inbound(p)
    await target_callback(p, replacement)
    p.plugin = await application(p)
    p.plugin.picking_task_completion_driver.clock = lambda: p.now
    await removed(p)
    await tick(p, p.plugin)
    await successful_put(p)
    await processor(p, p.plugin).process_batch()
    await report_result(p, "RECORDED")
    await processor(p, p.plugin).process_batch()
    await tick(p, p.plugin)
    async with p.sessions.begin() as db:
        # 来源服务结束是已有上游证据的显式前提，不重复投料验收。
        feed = await db.get(AutomaticPickingFeed, p.feed.id)
        feed.completed_source_member_ids = [feed.source_member_id]
        task = await db.get(PickingTask, p.task.id)
        task.last_applied_plan_revision = 1
        task.target_rack_id, task.target_rack_face = "OLD-TARGET", "A"
        task.initial_plan_evidence_id = task.last_plan_evidence_id = task.issued_evidence_id
    assert await completion_tick(p) == 1
    await answer(p, COMPLETION, {"result": "COMPLETED"}, publish=False)
    assert await completion_tick(p) == 1
    p.now += timedelta(seconds=1)
    await tick(p, p.plugin)
    conf = await confirmation(p, DEPARTURE)
    assert conf.picking_task_id == p.task.id
    assert conf.request_payload["data"]["rack_id"] == "TARGET"
    assert conf.request_payload["data"]["task_id"] == p.task.task_id
    async with p.sessions() as db:
        current = await db.scalar(select(AutomaticPickingFinalTargetRack))
        assert current.root_transport_task_id == replacement.transport_task_id
    await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-RETURN"}},
        publish=False,
    )
    await tick(p, p.plugin)
    assert (await out(p)).request_json["rack_id"] == "TARGET"


async def test_final_target_schema_roundtrip_preserves_live_obligation(database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    url, _ = database
    run_alembic("downgrade", "6781e26d6e19", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    await ready_departure(database)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_alembic("downgrade", "6781e26d6e19", database_url=url)
    assert "cannot discard current final-target departure obligations" in caught.value.stderr


async def test_first_departure_does_not_depend_on_closed_completion_history(database):
    from automatic_picking.application.return_rack_model import AutomaticPickingTaskCompletion

    from src.app.execution.models import InboundEvidence, WmsConfirmation

    p = await prepared_task(database)
    await initial_target(p)
    await complete(p)
    async with p.sessions.begin() as db:
        current = await db.scalar(select(AutomaticPickingTaskCompletion))
        confirmation_row = await db.get(WmsConfirmation, current.confirmation_id)
        evidence_row = await db.get(InboundEvidence, confirmation_row.response_evidence_id)
        await db.delete(current)
        await db.flush()
        await db.delete(confirmation_row)
        await db.flush()
        await db.delete(evidence_row)
    await tick(p, p.plugin)
    assert (await confirmation(p, DEPARTURE)).request_payload["data"]["task_id"] == p.task.task_id
