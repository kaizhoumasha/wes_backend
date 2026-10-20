"""实际组合根的搬运贯通；只落本地义务，外部派发一律拒绝。"""

from datetime import timedelta
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.return_model import AutomaticPickingReturn
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import select
from test_return_allocation_postgresql import arrived, observed, qualified
from test_return_motion_binding_postgresql import bound
from test_returns_postgresql import database, pending
from test_source_revision_postgresql import source

from deployment.plugin_composition import build_deployment_runtime
from src.app.device.contracts import EcsCommandResultReport
from src.app.device.models import Device
from src.app.device.models.command import DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def runtime(p, monkeypatch):
    # 丢弃加速通知；恢复只能依靠持久入口，禁止进程碰本机现有 broker。
    monkeypatch.setattr("deployment.plugin_composition.task_queue_gateway", None)
    provider = N(submit=AsyncMock(side_effect=AssertionError("offline must not dispatch")))
    service = TransportService(
        p.sessions,
        TransportRepository(),
        provider,
        result_timeout=timedelta(seconds=420),
        event_publisher=N(publish_to=AsyncMock(return_value=False)),
        progress_hook=RackInboundWindowService().on_transport_progress,
    )
    result = build_deployment_runtime(
        enabled_plugin_keys=("automatic-picking",),
        session_factory=p.sessions,
        transport_runtime=N(service=service, position_projection_service=object(), client=object()),
        device_command_service=DeviceCommandService(session_factory=p.sessions),
    )
    return result, service, provider


async def configured(p):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.position_bindings = {
            "FIVE_RACK": {"location_type": "RACK_POSITION", "location_id": "AUTO-FIVE"},
            "INLET": {"location_type": "HANDOFF_POSITION", "location_id": "AUTO-INLET"},
            "OUTLET": {"location_type": "HANDOFF_POSITION", "location_id": "AUTO-OUTLET"},
        }
        line.run_mode, line.flow_mode = "AUTO", None
        device = Device(device_code="SCAN4", device_name="offline transport scan", work_line_id=line.id)
        db.add(device)
        await db.flush()
        line.device_contracts = {
            **line.device_contracts,
            "SCAN4": {
                "device_id": device.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 10000,
                "status_max_age_ms": 10000,
            },
        }
        db.add(
            WorkLinePosition(
                workline_id=line.id,
                workline_code=line.line_code,
                position_code="AUTO-FIVE",
                position_type="RACK_POSITION",
                position_name="isolated transport",
                position_role="SMT_RETURN_RACK_POSITION",
                allowed_rack_kind="FIVE_LAYER",
                logic_location_code="AUTO-FIVE",
                capacity=2,
                enabled=True,
            )
        )


async def advance(p, app):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        return await app.plugins[0].picking_task_batch_driver.advance_completed_in_session(db, line)


async def event(p, role, stamp):
    device = "OFFLINE-SCAN3" if role == "SCAN3" else "SCAN4"
    identity = new_uuid7()
    payload = {
        "device_code": device,
        "contract_key": "FIXTURE_ONLY",
        "contract_version": "1",
        "source_event_id": identity,
        "event_type": "SCAN_COMPLETED",
        "timestamp": stamp,
        "is_debug": False,
        "data": {"bin_code": p.work.bin_code + "-B"},
    }
    async with p.sessions.begin() as db:
        fact = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="transport-scan:" + identity,
            payload_digest=canonical_json_digest(payload),
            normalized_payload=payload,
            received_at=p.now,
            apply_status="APPLIED",
            workline_id=p.line.id,
            device_code=device,
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            source_event_id=identity,
        )
        db.add(fact)
        await db.flush()
        return fact.id


async def test_actual_composition_no_work_to_fifo_drain_and_inbound_recovers_without_dispatch(pending, monkeypatch):
    from test_drain_progress_postgresql import decided

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = pending
    await configured(p)
    app, service, provider = runtime(p, monkeypatch)
    assert await app.execution.fact_processor.process_batch() == 1
    await advance(p, app)
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == work.exit_command_code))
        assert command.task_type == "MOVE_FORWARD" and command.params == {}
        assert command.device_code == "OFFLINE-SCAN2" and command.material_execution_id is None
    scan3 = await event(p, "SCAN3", 1000)
    assert await app.execution.fact_processor.process_batch() == 1
    async with p.sessions() as db:
        returned = await db.scalar(select(AutomaticPickingReturn))
        p.return_id = returned.id
        assert returned.scan3_evidence_id == scan3
    # 重建真正的组合，不通过测试重新注入任何中间 flow/候选。
    app, _, _ = runtime(p, monkeypatch)
    scan4 = await event(p, "SCAN4", 2000)
    assert await app.execution.fact_processor.process_batch() == 1
    await advance(p, app)
    async with p.sessions() as db:
        returned = await db.get(AutomaticPickingReturn, p.return_id)
        code = returned.scan4_command_code
        assert returned.scan4_evidence_id == scan4 and returned.ready_evidence_id is None
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        command.status = "DISPATCHING"  # 显式离线前提：结果来自已开始派发的原命令。
    evidence = DeviceEvidenceService(session_factory=p.sessions)
    await evidence.accept_result(
        EcsCommandResultReport(
            command_code=code,
            device_code="SCAN4",
            result="SUCCESS",
            finish_time=2100,
            data={},
        )
    )
    assert await evidence.process_one()
    assert await app.execution.fact_processor.process_batch() == 1
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.ready_evidence_id is not None
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await advance(p, app)
    async with p.sessions() as db:
        requests = list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(
                        WmsConfirmation.operation == "workline.return_buffer.drain_rack_decide@v1"
                    )
                )
            ).all()
        )
        assert len(requests) == 1
        assert requests[0].request_payload["data"] == {"workline_code": p.line.line_code, "required_slot_count": 1}
    await decided(p, "READY", publish=False)
    assert await app.execution.fact_processor.process_batch() == 1
    await advance(p, app)
    app, _, _ = runtime(p, monkeypatch)
    await advance(p, app)
    async with p.sessions() as db:
        actions = list((await db.scalars(select(TransportTask))).all())
        assert len(actions) == 1
        assert actions[0].status == "PENDING"
        assert actions[0].request_json["rcs_template_id"] == "CTU01"
        assert actions[0].request_json["target"] == {"kind": "RACK_POSITION", "location_code": "AUTO-FIVE"}
        assert actions[0].request_json["target_face"] == "A"
        assert len(list((await db.scalars(select(WmsConfirmation))).all())) == 2
    # 到位、WMS 分配是显式离线结果；业务应用仍走原 owner，不另造中间状态。
    from test_drain_inbound_postgresql import result as rack_result
    from test_return_allocation_postgresql import result as return_result

    from src.app.transport.models import TransportMember

    batch = app.plugins[0].picking_task_batch_driver
    p.app = (batch, app.plugins[0].runtime_binding.business_evidence_consumer, batch.drain_flow)
    p.inbound_service = service
    await rack_result(p, actions[0], location="AUTO-FIVE")
    await advance(p, app)
    await advance(p, app)  # 到位交接提交后，下轮持久扫描发现当前架面。
    await return_result(p)
    assert await app.execution.fact_processor.process_batch() == 1
    await advance(p, app)
    app, _, _ = runtime(p, monkeypatch)
    await advance(p, app)
    async with p.sessions() as db:
        move = await db.scalar(select(TransportTask).where(TransportTask.kind == "BIN_MOVE"))
        assert move is not None and move.status == "PENDING"
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == move.transport_task_id)
        )
        assert member.source_json == {"kind": "HANDOFF_POSITION", "location_code": "AUTO-OUTLET"}
        assert member.target_json == {
            "kind": "RACK_BIN_SLOT",
            "rack_id": "OFFLINE-DRAIN",
            "rack_face": "A",
            "slot_id": "S1",
        }
        assert len(list((await db.scalars(select(TransportTask))).all())) == 2
    provider.submit.assert_not_awaited()


async def test_actual_composition_reuses_host_source_inbound_owner(source, monkeypatch):
    p = source
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.position_bindings = {
            **line.position_bindings,
            "OUTLET": {"location_type": "HANDOFF_POSITION", "location_id": "AUTO-OUTLET"},
        }
    app, _, provider = runtime(p, monkeypatch)
    assert await app.picking_task_plan_activation_service.activate_batch() == 0
    async with p.sessions() as db:
        tasks = list((await db.scalars(select(TransportTask))).all())
        assert len(tasks) == 2  # 原来源架和目标架都由宿主计划 owner 创建。
        assert all(t.request_json["rcs_template_id"] in {"CTU01", "F01"} for t in tasks)
        source_task = next(t for t in tasks if t.request_json["rack_id"] == "SOURCE-1")
        assert source_task.transport_task_id == p.ingress.transport_task_id
        assert source_task.request_json["target"]["location_code"] == "AUTO-FIVE"
    provider.submit.assert_not_awaited()


async def test_actual_composition_source_rotation_then_departure_uses_current_readers(bound, monkeypatch):
    from test_return_before_motion_postgresql import phase
    from test_source_rack_departure_decision_postgresql import decided
    from test_source_rack_motion_postgresql import finish_next_face, rotation_application, rotation_result

    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = bound
    # 起点为显式已确认的来源服务结束检查点；其后不再注入候选或替换 flow。
    await rotation_application(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.position_bindings = {
            **line.position_bindings,
            "OUTLET": {"location_type": "HANDOFF_POSITION", "location_id": "OFFLINE-OUTLET"},
        }
        task = await db.get(PickingTask, p.task.id)
        task.last_applied_plan_revision = 2
        task.target_rack_id, task.target_rack_face = "OFFLINE-TARGET", "A"
        task.initial_plan_evidence_id = task.last_plan_evidence_id = task.issued_evidence_id
    app, _, provider = runtime(p, monkeypatch)
    await advance(p, app)
    current = await phase(p)
    assert current.rotation_transport_task_id is not None and current.departure_operation_id is None
    batch, scan = app.plugins[0].picking_task_batch_driver, app.plugins[0].runtime_binding.business_evidence_consumer
    await rotation_result(p, batch, scan)
    app, _, _ = runtime(p, monkeypatch)
    await advance(p, app)
    assert (await phase(p)).archived_at is not None
    await finish_next_face(p)
    await advance(p, app)
    current = await phase(p)
    assert current.rack_face == "B" and current.departure_operation_id is not None
    await decided(p, "READY", publish=False)
    assert await app.execution.fact_processor.process_batch() >= 1
    await advance(p, app)
    current = await phase(p)
    assert current.departure_transport_task_id is not None
    app, _, _ = runtime(p, monkeypatch)
    await advance(p, app)
    async with p.sessions() as db:
        rotation = await db.scalar(select(TransportTask).where(TransportTask.kind == "RACK_ROTATE"))
        departure = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current.departure_transport_task_id)
        )
        assert rotation.request_json["rcs_template_id"] == "CTU02" and rotation.request_json["target_face"] == "B"
        assert departure.request_json["rcs_template_id"] == "CTU03"
        assert departure.request_json["target"] == {"kind": "RACK_POSITION", "location_code": "OFFLINE-OUT"}
    provider.submit.assert_not_awaited()
