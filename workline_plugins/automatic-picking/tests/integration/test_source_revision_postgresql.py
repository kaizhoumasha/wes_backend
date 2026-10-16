"""跨 revision 的来源业务身份与当前物理进场生命周期由插件验收。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.feed_repository import FeedRepository
from automatic_picking.definition import DEFINITION
from sqlalchemy import delete, select
from wes_plugin_sdk import wms_operations

from deployment.plugin_composition import build_deployment_runtime
from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.execution.services import InboundEvidenceService
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.outbound_picking.cancel_wire import PickingTaskCancelEvent
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PickingTaskPlanDeltaEvent
from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.picking_task_cancel import PickingTaskCancelService
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService
from src.app.workline.models import WorkLine
from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import seed_picking_owner
from tests.support.postgresql_heavy import migrated_database
from tests.support.transport_callbacks import record_valid_callback

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
SOURCE_STEP = "PICKING_TASK_BIN_SOURCE_RACK_IN"


def runtime_for(sessions, service):
    return build_deployment_runtime(
        session_factory=sessions,
        enabled_plugin_keys=("automatic-picking",),
        transport_runtime=N(service=service, position_projection_service=object(), client=object()),
        device_command_service=object(),
    )


@pytest_asyncio.fixture
async def source(caplog):
    async with migrated_database() as (_, sessions):
        now = timezone.now_for_db()
        async with sessions.begin() as db:
            task, line = await seed_picking_owner(
                db,
                operation_id=new_uuid7(),
                issued_operation_id=new_uuid7(),
                task_key="AUTO-REV-" + new_uuid7(),
                now=now,
                status="PREPARING",
            )
            task.task_type, line.line_type = "AUTO", "AUTO"
            line.is_active = True
            line.plugin_key, line.plugin_version = DEFINITION.plugin_key, DEFINITION.plugin_version
            # 位置与时长仅为隔离测试输入，不提供设备映射或首批期限默认值。
            line.position_bindings = {
                "FIVE_RACK": {"location_type": "RACK_POSITION", "location_id": "AUTO-FIVE"},
                "TRANSFER_RACK": {"location_type": "RACK_POSITION", "location_id": "AUTO-TARGET"},
                "INLET": {"location_type": "HANDOFF_POSITION", "location_id": "AUTO-INLET"},
            }
            for role, location, kind in (
                ("SMT_SORTER_STATION", "AUTO-FIVE", "FIVE_LAYER"),
                ("SMT_TRANSFER_RACK_POSITION", "AUTO-TARGET", "TRANSFER"),
            ):
                db.add(
                    WorkLinePosition(
                        workline_id=line.id,
                        workline_code=line.line_code,
                        position_code=location,
                        position_name=location,
                        logic_location_code=location,
                        position_role=role,
                        allowed_rack_kind=kind,
                        capacity=1,
                    )
                )
            intent = wms_operations.outbound_picking_task_prepare(
                operation_id=new_uuid7(),
                task_id=task.task_id,
                work_line_code=line.line_code,
            )
            request = encode_request(intent, timestamp=1)
            response = await InboundEvidenceService().accept(
                db,
                kind="WMS_RESULT",
                source_identity=request["operation"] + ":" + intent.operation_id,
                normalized_payload={
                    "operation_id": intent.operation_id,
                    "timestamp": 1,
                    "code": "PREPARE_ACCEPTED",
                    "data": {},
                },
                received_at=now,
                operation=request["operation"],
                operation_id=intent.operation_id,
                apply_status="APPLIED",
            )
            response.evidence.processed_at = now
            db.add(
                WmsConfirmation(
                    operation=request["operation"],
                    operation_id=intent.operation_id,
                    picking_task_id=task.id,
                    request_payload=request,
                    request_digest=canonical_json_digest(request),
                    deadline_at=now + timedelta(seconds=30),
                    status="COMPLETED",
                    response_evidence_id=response.evidence.id,
                    response_result="PREPARE_ACCEPTED",
                    completed_at=now,
                )
            )
        service = TransportService(sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
        source = N(sessions=sessions, task=task, line=line, now=now, service=service)
        source.runtime = runtime_for(sessions, service)
        await add_revision(source, 1)
        assert await source.runtime.picking_task_plan_activation_service.activate_batch() == 2
        async with sessions() as db:
            source.ingress = await db.scalar(
                select(TransportTask)
                .join(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(TransportDecisionBinding.step == SOURCE_STEP)
            )
            assert source.ingress is not None
        yield source
        assert not any(
            record.name.endswith("picking_task_plan_activation") and record.levelno >= 40
            for record in caplog.get_records("setup") + caplog.get_records("call")
        ), "计划扫描吞掉异常时不能将返回 0 当作恢复成功"


async def add_revision(source, revision, face="A"):
    data = {
        "task_id": source.task.task_id,
        "plan_revision": revision,
        "added_bin_source_racks": [{"rack_id": "SOURCE-1", "rack_face": [face]}],
    }
    if revision == 1:
        data["target_rack"] = {"rack_id": "TARGET-1", "rack_face": "A"}
    event = PickingTaskPlanDeltaEvent.model_validate(
        {
            "operation": "outbound.picking_task.plan_delta@v1",
            "operation_id": new_uuid7(),
            "timestamp": 1,
            "data": data,
        }
    )
    result = await PickingTaskPlanDeltaService(source.sessions).record(event, received_at=source.now)
    assert result.code == "RECEIVED"
    return event


async def arrive(source, *, publish=True):
    await record_valid_callback(
        source.service,
        operation_id=new_uuid7(),
        transport_task_id=source.ingress.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=123,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "SOURCE-1",
            "status": "SUCCEEDED",
            "arrival_face": "A",
            "final_position": {"kind": "RACK_POSITION", "location_code": "AUTO-FIVE"},
        },
    )
    assert await source.service.process_pending_evidence(10) == 1
    if publish:
        await publish_arrival(source)


async def publish_arrival(source):
    publisher = InstalledPluginTransportOutcomePublisher(
        source.sessions,
        source.runtime.plugins,
        queue_gateway=N(enqueue_picking_task_plans=lambda: None),
    )
    assert await source.service.publish_pending_outcomes(10, publisher) == 1


async def snapshot(source):
    async with source.sessions() as db:
        members = list((await db.scalars(select(PickingTaskBinSourceRack).order_by(PickingTaskBinSourceRack.id))).all())
        feeds = list((await db.scalars(select(AutomaticPickingFeed).order_by(AutomaticPickingFeed.id))).all())
        requests = list(
            (
                await db.scalars(
                    select(WmsConfirmation)
                    .where(
                        WmsConfirmation.operation == BIN_INBOUND_BATCH_OPERATION,
                    )
                    .order_by(WmsConfirmation.id)
                )
            ).all()
        )
        ingress = list(
            (
                await db.scalars(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.step == SOURCE_STEP,
                    )
                )
            ).all()
        )
        return members, feeds, requests, ingress


async def test_same_face_revisions_reuse_ingress_and_recover_each_member_once(source):
    await arrive(source)
    assert await source.runtime.picking_task_plan_activation_service.activate_batch() == 1
    first_members, first_feeds, first_requests, first_ingress = await snapshot(source)
    second_plan = await add_revision(source, 2)
    assert (
        await PickingTaskPlanDeltaService(source.sessions).record(second_plan, received_at=source.now)
    ).code == "DUPLICATE"
    # 重装配和并发扫描代替丢失的计划/发布通知，唯一 owner 仍为持久计划扫描。
    restored = runtime_for(source.sessions, source.service)
    assert sorted(
        await asyncio.gather(
            source.runtime.picking_task_plan_activation_service.activate_batch(),
            restored.picking_task_plan_activation_service.activate_batch(),
        )
    ) == [0, 1]
    assert await restored.picking_task_plan_activation_service.activate_batch() == 0
    members, feeds, requests, ingress = await snapshot(source)
    assert [m.plan_revision for m in members] == [1, 2]
    assert [f.source_member_id for f in feeds] == [m.id for m in members]
    assert len({f.operation_id for f in feeds}) == 2
    assert [r.request_payload["data"] for r in requests] == [
        {"task_id": source.task.task_id, "plan_revision": revision, "rack_id": "SOURCE-1", "rack_face": "A"}
        for revision in (1, 2)
    ]
    assert [r.operation_id for r in requests] == [f.operation_id for f in feeds]
    assert (members[0].id, feeds[0].id, requests[0].operation_id) == (
        first_members[0].id,
        first_feeds[0].id,
        first_requests[0].operation_id,
    )
    assert len(ingress) == len(first_ingress) == 1
    assert ingress[0].client_request_id == source.ingress.client_request_id
    assert ingress[0].source_evidence_id == members[0].source_evidence_id != members[1].source_evidence_id
    assert ingress[0].window_target_location_code == "AUTO-FIVE" and ingress[0].window_released_at is None
    async with source.sessions() as db:
        assert len((await db.scalars(select(TransportTask))).all()) == 2  # 目标架和来源架各一次。
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "SOURCE-1"))
        assert position.source_transport_task_id == source.ingress.transport_task_id and position.arrival_face == "A"


@pytest.mark.parametrize("guard", ["unpublished", "unknown", "different_face"])
async def test_new_revision_waits_for_current_published_position_and_actual_face(source, guard):
    await arrive(source, publish=guard != "unpublished")
    await add_revision(source, 2, face="B" if guard == "different_face" else "A")
    if guard == "unknown":
        async with source.sessions.begin() as db:
            position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "SOURCE-1"))
            position.position_unknown = True
    expected = 1 if guard == "different_face" else 0
    assert await source.runtime.picking_task_plan_activation_service.activate_batch() == expected
    members, feeds, requests, ingress = await snapshot(source)
    assert len(ingress) == 1 and ingress[0].client_request_id == source.ingress.client_request_id
    assert len(feeds) == len(requests) == expected
    assert all(feed.source_member_id == members[0].id for feed in feeds)
    if guard == "unpublished":
        await publish_arrival(source)
        assert (
            await runtime_for(source.sessions, source.service).picking_task_plan_activation_service.activate_batch()
            == 2
        )
        assert len((await snapshot(source))[1]) == 2
    else:
        assert await source.runtime.picking_task_plan_activation_service.activate_batch() == 0
    # 异面不会据计划伪造到位、旋转或新的进场 Transport。
    async with source.sessions() as db:
        assert len((await db.scalars(select(TransportTask))).all()) == 2


async def test_revision_feed_and_confirmation_rollback_and_restart_together(source):
    await arrive(source)
    assert await source.runtime.picking_task_plan_activation_service.activate_batch() == 1
    await add_revision(source, 2)
    before = await snapshot(source)
    with pytest.raises(RuntimeError, match="crash before commit"):
        async with source.sessions.begin() as db:
            line = await db.get(WorkLine, source.line.id)
            task = await db.get(PickingTask, source.task.id)
            assert await source.runtime.plugins[0].picking_task_batch_driver.advance_in_session(db, line, task) == 1
            assert len((await db.scalars(select(AutomaticPickingFeed))).all()) == 2
            assert (
                len(
                    (
                        await db.scalars(
                            select(WmsConfirmation).where(
                                WmsConfirmation.operation == BIN_INBOUND_BATCH_OPERATION,
                            )
                        )
                    ).all()
                )
                == 2
            )
            raise RuntimeError("crash before commit")
    after = await snapshot(source)
    assert [f.operation_id for f in after[1]] == [f.operation_id for f in before[1]]
    assert [r.operation_id for r in after[2]] == [r.operation_id for r in before[2]]
    restored = runtime_for(source.sessions, source.service)
    assert await restored.picking_task_plan_activation_service.activate_batch() == 1
    assert await restored.picking_task_plan_activation_service.activate_batch() == 0
    _, feeds, requests, ingress = await snapshot(source)
    assert len(feeds) == len(requests) == 2 and len(ingress) == 1
    assert requests[1].operation_id == feeds[1].operation_id


async def test_cancelled_old_revision_result_cannot_feed_or_satisfy_new_member(source):
    await arrive(source)
    assert await source.runtime.picking_task_plan_activation_service.activate_batch() == 1
    _, old_feeds, old_requests, _ = await snapshot(source)
    old_feed, old_request = old_feeds[0], old_requests[0]
    event = PickingTaskCancelEvent.model_validate(
        {
            "operation": "outbound.picking_task.cancel@v1",
            "operation_id": new_uuid7(),
            "timestamp": 2,
            "data": {
                "task_id": source.task.task_id,
                "cancel_scope": "PLAN_MEMBERS",
                "bin_source_racks": [
                    {"plan_revision": 1, "rack_id": "SOURCE-1", "rack_face": ["A"]},
                ],
            },
        }
    )
    assert (
        await PickingTaskCancelService(source.sessions, transport_service=source.service).record(
            event,
            received_at=source.now,
        )
    ).code == "RECEIVED"
    await add_revision(source, 2)
    # 取消后才到达的旧 READY 仍按旧请求解释；只停止旧成员尚未创建的投料。
    async with source.sessions.begin() as db:
        evidence = await InboundEvidenceService().accept(
            db,
            kind="WMS_RESULT",
            source_identity=BIN_INBOUND_BATCH_OPERATION + ":" + old_request.operation_id,
            normalized_payload={
                "operation_id": old_request.operation_id,
                "timestamp": 2,
                "code": "DECIDED",
                "data": {
                    "result": "READY",
                    "bins": [
                        {
                            "bin_code": "A123456789",
                            "source_locator": {
                                "type": "RACK_BIN_SLOT",
                                "rack_id": "SOURCE-1",
                                "rack_face": "A",
                                "slot_id": "S1",
                            },
                        }
                    ],
                },
            },
            received_at=source.now,
            workline_id=source.line.id,
            operation=BIN_INBOUND_BATCH_OPERATION,
            operation_id=old_request.operation_id,
            apply_status="APPLIED",
        )
        confirmation = await db.get(WmsConfirmation, old_request.id)
        confirmation.status, confirmation.response_result = "COMPLETED", "READY"
        confirmation.response_evidence_id, confirmation.completed_at = evidence.evidence.id, source.now
    assert await runtime_for(source.sessions, source.service).picking_task_plan_activation_service.activate_batch() == 3
    # 同批还会消费进场回执；以旧结果的持久发布事实判断能否清理。
    await runtime_for(source.sessions, source.service).execution.fact_processor.process_batch()
    members, feeds, requests, ingress = await snapshot(source)
    assert members[0].cancelled_evidence_id is not None and members[1].cancelled_evidence_id is None
    assert (feeds[0].id, feeds[0].phase, feeds[0].offset, feeds[0].transport_task_id) == (
        old_feed.id,
        "CANCELLED",
        0,
        None,
    )
    assert feeds[1].phase == "REQUESTED" and feeds[1].response_evidence_id is None
    assert requests[1].status == "PENDING" and requests[1].operation_id != old_request.operation_id
    async with source.sessions.begin() as db:
        old_result = await db.get(InboundEvidence, feeds[0].response_evidence_id)
        assert old_result.published_at is not None and old_result.decision_digest is not None
        new_feed = await db.get(AutomaticPickingFeed, feeds[1].id)
        assert await FeedRepository().inbound(db, new_feed) is None
        with pytest.raises(ValueError, match="differs from frozen plan member"):
            await FeedRepository().inbound(
                db,
                N(
                    operation_id=old_request.operation_id,
                    workline_id=new_feed.workline_id,
                    picking_task_id=new_feed.picking_task_id,
                    source_member_id=new_feed.source_member_id,
                ),
            )
        # 已终态旧 Feed 不再恢复原 Confirmation；可清理闭合交付历史，保留当前成员/进场依据。
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.id == old_request.id))
    assert await runtime_for(source.sessions, source.service).picking_task_plan_activation_service.activate_batch() == 0
    await add_revision(source, 3)
    assert await runtime_for(source.sessions, source.service).picking_task_plan_activation_service.activate_batch() == 1
    _, current_feeds, current_requests, current_ingress = await snapshot(source)
    assert [f.id for f in current_feeds[:2]] == [f.id for f in feeds]
    assert [r.operation_id for r in current_requests] == [requests[1].operation_id, current_feeds[2].operation_id]
    assert [r.request_payload["data"]["plan_revision"] for r in current_requests] == [2, 3]
    assert current_ingress[0].client_request_id == ingress[0].client_request_id
    async with source.sessions() as db:
        assert len((await db.scalars(select(TransportTask))).all()) == 2
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "SOURCE-1"))
        assert position.source_transport_task_id == source.ingress.transport_task_id and not position.position_unknown
