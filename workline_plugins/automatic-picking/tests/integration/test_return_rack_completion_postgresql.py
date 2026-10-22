"""退料架与正常完成整段验收；精确动作/位置只作为离线输入。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
from automatic_picking.application.pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from automatic_picking.application.return_rack_model import AutomaticPickingReturnRack, AutomaticPickingTaskCompletion
from automatic_picking.application.return_rack_repository import RETURN_RACK_IN, RETURN_RACK_OUT, RETURN_RACK_ROTATE
from sqlalchemy import delete, func, select
from test_direct_pick_progress_postgresql import admitted, application, prepared_direct
from test_material_close_postgresql import report_result
from test_material_flow_postgresql import result
from test_normal_picking_postgresql import processor, tick
from test_pickup_identity_postgresql import TARGET, database
from test_placement_report_postgresql import successful_put
from test_rotated_target_put_postgresql import removed
from test_target_put_postgresql import current_pickup
from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

from src.app.execution.models import InboundEvidence, MaterialExecution, TransportDecisionBinding, WmsConfirmation
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.transport.models import TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTask
from src.app.workline.models import WorkLine
from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ARRIVAL = "outbound.return_rack.arrival_report@v1"
COMPLETION = "outbound.picking_task.completion_confirm@v1"
DEPARTURE = "outbound.rack.departure_decide@v1"


@pytest.fixture(autouse=True)
def offline_wakeups(monkeypatch):
    monkeypatch.setattr(
        "src.app.wms_integration.outbound_picking.services.rack_departure.task_queue_gateway",
        N(enqueue_wms_confirmations=lambda: None),
    )


async def prepared(database):
    p = await prepared_direct(database)
    async with p.sessions.begin() as db:
        task = await db.get(PickingTask, p.task.id)
        task.last_applied_plan_revision = 1
        task.target_rack_id, task.target_rack_face = "TARGET", "A"
        task.initial_plan_evidence_id = task.last_plan_evidence_id = task.issued_evidence_id
        p.task = task
    p.plugin = await application(p)
    p.plugin.picking_task_completion_driver.clock = lambda: p.now
    p.plugin.picking_task_batch_driver.return_rack_flow.clock = lambda: p.now
    p.service = TransportService(
        p.sessions,
        TransportRepository(),
        N(),
        result_timeout=timedelta(seconds=420),
        progress_hook=RackInboundWindowService().on_transport_progress,
    )
    return p


async def confirmation(p, operation):
    async with p.sessions() as db:
        return await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.operation == operation).order_by(WmsConfirmation.id.desc())
        )


async def answer(p, operation, data=None, *, code="DECIDED", publish=True):
    conf = await confirmation(p, operation)
    assert conf is not None
    payload = {"operation_id": conf.operation_id, "code": code, "timestamp": 1}
    if data is not None:
        payload["data"] = data
    elif operation == ARRIVAL:
        payload["data"] = {}
    async with p.sessions.begin() as db:
        live = await db.get(WmsConfirmation, conf.id)
        evidence = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=operation + ":" + conf.operation_id,
            operation=operation,
            operation_id=conf.operation_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            received_at=p.now,
            processed_at=None if conf.workline_id is not None else p.now,
            apply_status="APPLIED",
            picking_task_id=conf.picking_task_id,
            workline_id=conf.workline_id,
            published_at=None,
        )
        db.add(evidence)
        await db.flush()
        live.status, live.response_result = "COMPLETED", data["result"] if data else code
        live.response_evidence_id, live.completed_at = evidence.id, p.now
        result_id = evidence.id
    if publish and conf.workline_id is not None:
        await processor(p, p.plugin).process_batch()
        async with p.sessions() as db:
            assert (await db.get(InboundEvidence, result_id)).published_at is not None
    return result_id


async def completion_tick(p):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        task = await db.get(PickingTask, p.task.id, with_for_update=True)
        return await p.plugin.picking_task_completion_driver.advance_in_session(db, line, task, now=p.now)


async def completed_member(p):
    # 当前成员的报告完成事实；不构造 Work，也不声称取盘设备协议已经冻结。
    async with p.sessions.begin() as db:
        current = await db.scalar(
            select(AutomaticPickingDirectPick).where(AutomaticPickingDirectPick.member_id == p.member.id)
        )
        if current is None:
            db.add(
                AutomaticPickingDirectPick(
                    workline_id=p.line.id,
                    picking_task_id=p.task.id,
                    member_id=p.member.id,
                    completed_evidence_id=p.task.issued_evidence_id,
                )
            )
        else:
            current.completed_evidence_id = p.task.issued_evidence_id


async def inbound(p, *, correlation="offline-return-in", face="F2"):
    flow = p.plugin.picking_task_batch_driver.return_rack_flow
    async with p.sessions.begin() as db:
        handle = await flow.creator.create(
            db,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.member.source_evidence_id,
            correlation_id=correlation,
            step=RETURN_RACK_IN,
            resource_fence_id=p.member.rack_id,
            intent=N(
                rack_id=p.member.rack_id,
                source=TransportRackPosition("OFFLINE-STORAGE"),
                target=TransportRackPosition("DIRECT-RETURN_RACK"),
                target_face=face,
                rcs_template_id=TransportRcsTemplateId.F01,
            ),
        )
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        binding = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == handle.client_request_id
            )
        )
        binding.window_target_location_code = "DIRECT-RETURN_RACK"
    await callback(p, task)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        current = await db.scalar(
            select(AutomaticPickingReturnRack).where(
                AutomaticPickingReturnRack.root_transport_task_id == handle.transport_task_id
            )
        )
        assert current is not None and current.arrival_confirmation_id is not None
        return current


async def callback(p, task, *, publish=True):
    data = {
        "transport_task_id": task.transport_task_id,
        "kind": task.kind,
        "outcome_revision": 1,
        "rack_id": task.request_json["rack_id"],
        "status": "SUCCEEDED",
        "final_position": task.request_json.get("target", task.request_json.get("position")),
        "arrival_face": task.request_json.get("target_face") or "F2",
    }
    operation_id = new_uuid7()
    message = {"operation": "transport.task.resulted@v1", "operation_id": operation_id, "timestamp": 1, "data": data}
    assert (
        await p.service.record_callback(
            operation_id=operation_id,
            operation=message["operation"],
            message=message,
            payload=data,
            rejection_reason_code=None,
        )
    )["code"] == "RECEIVED"
    assert await p.service.process_pending_evidence(100) == 1
    if publish:
        publisher = InstalledPluginTransportOutcomePublisher(
            p.sessions,
            (p.plugin,),
            queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
        )
        assert await p.service.publish_pending_outcomes(100, publisher) >= 1
        await processor(p, p.plugin).process_batch()


async def test_single_report_close_drives_task_completion_without_duplicate_scope_or_clearance(database):
    from dataclasses import asdict

    p = await prepared(database)
    decide = await admitted(p, p.plugin)
    await result(p, decide, data={"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}})
    await processor(p, p.plugin).process_batch()
    await removed(p)
    await tick(p, p.plugin)
    await successful_put(p)
    await processor(p, p.plugin).process_batch()
    assert await completion_tick(p) == 0
    await report_result(p, "RECORDED")
    await processor(p, p.plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        assert (await db.scalar(select(AutomaticPickingDirectPick))).completed_evidence_id is not None
    # 报告/关闭证据复用原整段验收；这里只验收接回任务完成，不再等待面/架重复通知。
    assert await completion_tick(p) == 1
    evidence_id = await answer(p, COMPLETION, {"result": "COMPLETED"}, publish=False)
    assert await completion_tick(p) == 1
    async with p.sessions() as db:
        assert (await db.get(PickingTask, p.task.id)).status == "EXECUTION_COMPLETED"
        assert (await db.get(InboundEvidence, evidence_id)).apply_status == "APPLIED"
    assert await completion_tick(p) == 0


async def test_arrival_report_then_departure_wait_and_original_out_survive_task_completion(database):
    p = await prepared(database)
    await completed_member(p)
    current = await inbound(p)
    assert await completion_tick(p) == 0
    await answer(p, ARRIVAL, code="RECORDED")
    await answer(p, DEPARTURE, {"result": "WAIT", "retry_after_ms": 1000})
    old = await confirmation(p, DEPARTURE)
    await tick(p, p.plugin)
    assert (await confirmation(p, DEPARTURE)).id == old.id
    p.now += timedelta(seconds=1)
    await tick(p, p.plugin)
    assert (await confirmation(p, DEPARTURE)).id != old.id
    await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-STORAGE"}},
    )
    await tick(p, p.plugin)
    async with p.sessions() as db:
        rack = await db.get(AutomaticPickingReturnRack, current.id)
        out = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == rack.departure_transport_task_id)
        )
        assert out is not None and out.request_json["rcs_template_id"] == "F01"
    # 未清场也可以正常完成；原离场由无 EXECUTING 的 batch 前缀继续收口。
    assert await completion_tick(p) == 1
    await answer(p, COMPLETION, {"result": "COMPLETED"})
    assert await completion_tick(p) == 1
    # 接纳释放原窗口；清理已闭合原进场后只靠原离场身份恢复。
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    class OfflineProvider:
        async def submit(self, **kwargs):
            assert kwargs["transport_task_id"] == out.transport_task_id
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.service.provider = OfflineProvider()
    assert await p.service.submit_pending_tasks(1) == 1
    async with p.sessions.begin() as db:
        root = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current.root_transport_task_id)
        )
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == root.client_request_id)
        )
        assert binding.window_released_at is not None
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == root.transport_task_id))
        await db.delete(binding)
        await db.delete(root)
    await callback(p, out, publish=False)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnRack, current.id)).closed_evidence_id is None
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (p.plugin,),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    await p.service.publish_pending_outcomes(100, publisher)
    await processor(p, p.plugin).process_batch()
    p.plugin = await application(p)
    await tick(p, p.plugin)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturnRack, current.id)).closed_evidence_id is not None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TransportDecisionBinding)
                .where(TransportDecisionBinding.step == RETURN_RACK_OUT)
            )
            == 1
        )


async def test_rotation_to_pending_face_and_scope_completion_only_stop_new_pickups(database):
    p = await prepared(database)
    await completed_member(p)
    async with p.sessions.begin() as db:
        db.add(
            DirectPickExecution(
                picking_task_id=p.task.id,
                rack_id=p.member.rack_id,
                rack_face="F3",
                slot_id="S2",
                plan_revision=1,
                source_evidence_id=p.member.source_evidence_id,
            )
        )
    current = await inbound(p)
    async with p.sessions() as db:
        rack = await db.get(AutomaticPickingReturnRack, current.id)
        rotation = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == rack.rotation_transport_task_id)
        )
        assert rotation is not None and rotation.request_json["target_face"] == "F3"
        assert await confirmation(p, DEPARTURE) is None
    await callback(p, rotation)
    await tick(p, p.plugin)
    async with p.sessions() as db:
        rack = await db.get(AutomaticPickingReturnRack, current.id)
        assert rack.arrival_transport_task_id == rotation.transport_task_id
        assert rack.rotation_transport_task_id is None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TransportDecisionBinding)
                .where(TransportDecisionBinding.step == RETURN_RACK_ROTATE)
            )
            == 1
        )


async def test_completion_current_creation_is_atomic_and_concurrent(database, monkeypatch):
    p = await prepared(database)
    await completed_member(p)
    flow = p.plugin.picking_task_completion_driver
    original = flow.scheduler.create_in_session

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("after confirmation before current")

    with monkeypatch.context() as patch:
        patch.setattr(flow.scheduler, "create_in_session", crash)
        with pytest.raises(RuntimeError, match="before current"):
            await completion_tick(p)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingTaskCompletion)) == 0
        assert await confirmation(p, COMPLETION) is None
    assert sum(await asyncio.gather(*(completion_tick(p) for _ in range(3)))) == 1
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingTaskCompletion)) == 1
    await answer(p, COMPLETION, {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 1000})
    old = await confirmation(p, COMPLETION)
    assert await completion_tick(p) == 0
    p.now += timedelta(seconds=1)
    assert sum(await asyncio.gather(*(completion_tick(p) for _ in range(3)))) == 1
    assert (await confirmation(p, COMPLETION)).id != old.id
    await answer(p, COMPLETION, {"result": "PLAN_REVISION_STALE", "current_plan_revision": 2})
    assert await completion_tick(p) == 0
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).last_applied_plan_revision = 2
    assert await completion_tick(p) == 1


async def scope_event(p, *, through=1):
    from src.app.wms_adapter.return_rack_task_completion.wire import ReturnRackTaskCompletedEvent
    from src.app.wms_integration.return_rack_task_completion.service import ReturnRackTaskCompletedService

    event = ReturnRackTaskCompletedEvent.model_validate(
        {
            "operation": "outbound.return_rack.task_completed@v1",
            "operation_id": new_uuid7(),
            "timestamp": 1000,
            "data": {
                "task_id": p.task.task_id,
                "through_plan_revision": through,
                "rack_id": p.member.rack_id,
                "completed_at": 1000,
            },
        }
    )
    service = ReturnRackTaskCompletedService(session_factory=p.sessions)
    assert (await service.record(event, received_at=p.now)).code == "RECEIVED"
    await processor(p, p.plugin).process_batch()
    async with p.sessions() as db:
        return await db.scalar(select(InboundEvidence).where(InboundEvidence.operation_id == event.operation_id))


async def test_scope_waits_for_cursor_and_survives_member_cleanup_without_fabricating_material_close(database):
    p = await prepared(database)
    fact = await scope_event(p, through=2)
    assert fact.published_at is None
    assert await completion_tick(p) == 0
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).last_applied_plan_revision = 2
    p.now += timedelta(seconds=1)
    await processor(p, p.plugin).process_batch()
    async with p.sessions.begin() as db:
        line, task = await db.get(WorkLine, p.line.id), await db.get(PickingTask, p.task.id)
        await p.plugin.picking_task_batch_driver.advance_in_session(db, line, task)
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 0
        await db.execute(delete(DirectPickExecution).where(DirectPickExecution.picking_task_id == task.id))
    # 新 operation 的重复范围事实不反查已清理成员。
    assert (await scope_event(p, through=2)).published_at is not None
    assert await completion_tick(p) == 1


async def test_scope_completion_preserves_existing_pickup_and_blocks_premature_task_completion(database):
    p = await prepared(database)
    await admitted(p, p.plugin)
    assert (await scope_event(p)).published_at is not None
    assert await completion_tick(p) == 0
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status != "CLOSED"
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 1
    await tick(p, p.plugin)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 1


async def test_normal_no_work_handoff_does_not_wait_for_scan3_or_clearing(database):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.work_model import AutomaticPickingWork

    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = await prepared(database)
    await completed_member(p)
    async with p.sessions.begin() as db:
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            rack_id="SOURCE",
            rack_face="A",
            plan_revision=1,
            source_evidence_id=p.task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        feed = AutomaticPickingFeed(
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_member_id=member.id,
            operation_id=new_uuid7(),
            phase="FEED_COMPLETE",
        )
        db.add(feed)
        await db.flush()
        db.add(
            AutomaticPickingWork(
                workline_id=p.line.id,
                picking_task_id=p.task.id,
                feed_id=feed.id,
                bin_code="A000000001",
                scan2_evidence_id=p.task.issued_evidence_id,
                scanned_at=1,
                operation_id=new_uuid7(),
                phase="EXIT_PENDING",
            )
        )
    assert await completion_tick(p) == 1


async def test_migration_roundtrip_and_live_current_downgrade_guard(database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    url, sessions = database
    run_alembic("downgrade", "81daa3e0047d", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    p = await prepared(database)
    await completed_member(p)
    assert await completion_tick(p) == 1
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_alembic("downgrade", "81daa3e0047d", database_url=url)
    assert "cannot discard current return-rack or task completion obligations" in failure.value.stderr
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingTaskCompletion)) == 1


async def test_host_plan_scan_does_not_recreate_inbound_for_completed_member_after_history_cleanup(database):
    from automatic_picking.definition import DEFINITION

    from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
        PickingTaskPlanActivationService,
    )

    p = await prepared(database)
    await completed_member(p)
    # 现有目标计划动作只用于宿主已决定身份；本例不派发或猜现场结果。
    creator = p.plugin.picking_task_batch_driver.return_rack_flow.creator
    async with p.sessions.begin() as db:
        await creator.create(
            db,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.task.issued_evidence_id,
            correlation_id="existing-target",
            step="PICKING_TASK_TARGET_RACK_IN",
            resource_fence_id="TARGET",
            intent=N(
                rack_id="TARGET",
                source=TransportRackPosition("STORE"),
                target=TransportRackPosition("DIRECT-TRANSFER_RACK"),
                target_face="A",
                rcs_template_id=TransportRcsTemplateId.F01,
            ),
        )

    class NoNewInbound:
        async def create_windowed_inbound(self, *args, **kwargs):
            raise AssertionError("completed member must not recreate inbound after closed-history cleanup")

    activation = PickingTaskPlanActivationService(p.sessions, plugins=(p.plugin,), transport_creator=NoNewInbound())
    await activation._activate_workline(
        p.line.id,
        handler=p.plugin.picking_task_plan_applied_handler,
        plugin_identity=(DEFINITION.plugin_key, DEFINITION.plugin_version),
    )
    assert await confirmation(p, COMPLETION) is not None


@pytest.mark.parametrize("next_face", ["F2", "F3"])
async def test_frozen_departure_defers_future_revision_until_next_actual_arrival(database, next_face):
    p = await prepared(database)
    await completed_member(p)
    current = await inbound(p)
    original = await confirmation(p, DEPARTURE)
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).last_applied_plan_revision = 2
        db.add(
            DirectPickExecution(
                picking_task_id=p.task.id,
                rack_id=p.member.rack_id,
                rack_face=next_face,
                slot_id="FUTURE",
                plan_revision=2,
                source_evidence_id=p.member.source_evidence_id,
            )
        )

    async def advance_future():
        await tick(p, p.plugin)
        async with p.sessions.begin() as db:
            line, task = await db.get(WorkLine, p.line.id), await db.get(PickingTask, p.task.id)
            await p.plugin.picking_task_batch_driver.advance_in_session(db, line, task)
            assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 0
            rack = await db.get(AutomaticPickingReturnRack, current.id)
            assert rack.rotation_transport_task_id is None

    await advance_future()
    assert (await confirmation(p, DEPARTURE)).id == original.id
    await answer(p, DEPARTURE, {"result": "WAIT", "retry_after_ms": 1000})
    await advance_future()
    assert (await confirmation(p, DEPARTURE)).id == original.id
    p.now += timedelta(seconds=1)
    await advance_future()
    assert (await confirmation(p, DEPARTURE)).id != original.id
    await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-STORAGE"}},
    )
    await advance_future()
    async with p.sessions() as db:
        out_id = (await db.get(AutomaticPickingReturnRack, current.id)).departure_transport_task_id
        assert out_id is not None
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult

    class OfflineProvider:
        async def submit(self, **kwargs):
            assert kwargs["transport_task_id"] == out_id
            return TransportSubmitResult(TransportSubmitCode.RECEIVED, kwargs["transport_task_id"])

    p.service.provider = OfflineProvider()
    assert await p.service.submit_pending_tasks(1) == 1
    # 原 OUT 尚无终态，新一轮已有精确到位就能独立承接未来成员。
    await inbound(p, correlation="new-round", face=next_face)
    async with p.sessions.begin() as db:
        line, task = await db.get(WorkLine, p.line.id), await db.get(PickingTask, p.task.id)
        await p.plugin.picking_task_batch_driver.advance_in_session(db, line, task)
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 1


async def test_rack_motion_waits_only_for_original_round_direct_return_obligations(database):
    from automatic_picking.application.return_allocation_model import AutomaticPickingReturnAllocation

    p = await prepared(database)
    await completed_member(p)
    # 当前轮回架先阻塞离场；原轮次身份由真实原进场 binding 固定。
    flow = p.plugin.picking_task_batch_driver.return_rack_flow
    current = await inbound(p)
    async with p.sessions.begin() as db:
        rack = await db.get(AutomaticPickingReturnRack, current.id)
        # 清除尚未派发的测试 departure 请求，仅为把断点停在首次准入前。
        request_id = rack.departure_confirmation_id
        rack.departure_confirmation_id = None
        await db.flush()
        await db.delete(await db.get(WmsConfirmation, request_id))
        _, root = await flow.repository.transport(db, rack.root_transport_task_id)
        rows = [
            AutomaticPickingReturnAllocation(
                workline_id=p.line.id,
                arrival_evidence_id=p.task.issued_evidence_id,
                rack_id=rack_id,
                rack_face="F2",
                operation_id=new_uuid7(),
                return_ids=[],
                created_at=created_at,
            )
            for rack_id, created_at in (
                (p.member.rack_id, root.created_at - timedelta(days=1)),
                (p.member.rack_id, root.created_at + timedelta(milliseconds=1)),
                ("INDEPENDENT", root.created_at),
            )
        ]
        db.add_all(rows)
        await db.flush()
        current_allocation_id = rows[1].id

    async def advance_rack():
        async with p.sessions.begin() as db:
            return await flow.advance_in_session(db, await db.get(WorkLine, p.line.id, with_for_update=True))

    await advance_rack()
    assert await confirmation(p, DEPARTURE) is None
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturnAllocation, current_allocation_id)).settled_at = p.now
    await advance_rack()
    assert await confirmation(p, DEPARTURE) is not None


async def test_return_departure_publisher_requires_original_published_ready_and_frozen_destination(database):
    from src.app.transport.service import _outcome_from_json

    p = await prepared(database)
    await completed_member(p)
    current = await inbound(p)
    ready_id = await answer(
        p,
        DEPARTURE,
        {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-STORAGE"}},
    )
    await tick(p, p.plugin)
    async with p.sessions() as db:
        rack = await db.get(AutomaticPickingReturnRack, current.id)
        out = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == rack.departure_transport_task_id)
        )
    await callback(p, out, publish=False)
    async with p.sessions.begin() as db:
        out = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == out.transport_task_id))
        outcome = _outcome_from_json(out.outcome_json)
        for drift in ("operation", "unpublished", "destination"):
            async with db.begin_nested() as savepoint:
                binding = await db.scalar(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.client_request_id == out.client_request_id
                    )
                )
                if drift == "operation":
                    binding.correlation_id = "departure:" + new_uuid7()
                elif drift == "unpublished":
                    (await db.get(InboundEvidence, ready_id)).published_at = None
                else:
                    out.request_json = {
                        **out.request_json,
                        "target": {"kind": "RACK_POSITION", "location_code": "WRONG"},
                    }
                await db.flush()
                with pytest.raises(ValueError, match=r"departure|published|READY"):
                    await p.plugin.transport_outcome_publisher.publish(db, outcome)
                await savepoint.rollback()
        assert not await p.plugin.transport_outcome_publisher.publish(db, outcome)
