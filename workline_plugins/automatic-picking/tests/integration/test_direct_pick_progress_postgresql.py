"""退料单盘整段离线业务验收；归属/到位/动作参数均由 fixture 明确提供。"""

import asyncio
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from automatic_picking.application.transport_composition import build_picking_plugin, build_transport_plugin
from automatic_picking.application.work_model import AutomaticPickingWork
from automatic_picking.definition import DEFINITION
from sqlalchemy import delete, func, select
from test_material_close_postgresql import report_result
from test_material_flow_postgresql import BARCODE, FixtureScanReader, result, scan
from test_normal_picking_postgresql import processor, tick
from test_pickup_identity_postgresql import TARGET, FixtureObservations, database
from test_placement_report_postgresql import FixturePutReader, successful_put
from test_target_put_postgresql import FixtureTargetReader, current_pickup, request
from test_work_and_task_progress_postgresql import seed

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest
from src.app.device.models.device import Device
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def prepared_direct(database):
    _, sessions = database
    task, line, _feed, _, now = await seed(sessions)
    p = N(
        sessions=sessions,
        task=task,
        line=line,
        now=now,
        observations=FixtureObservations(),
        target_ready=True,
        request_changes={},
        observation_changes={},
        identity_available=True,
    )
    async with sessions.begin() as db:
        await db.execute(delete(AutomaticPickingFeed))
        await db.execute(delete(PickingTaskBinSourceRack))
        member = DirectPickExecution(
            picking_task_id=task.id,
            rack_id="RETURN-RACK",
            rack_face="F2",
            slot_id="REEL-SLOT",
            plan_revision=1,
            source_evidence_id=task.issued_evidence_id,
        )
        db.add(member)
        live = await db.get(WorkLine, line.id)
        live.is_active = True
        live.position_bindings = {
            slot.slot_key: {"location_type": slot.location_type, "location_id": "DIRECT-" + slot.slot_key}
            for slot in DEFINITION.position_slots
        }
        for code in ("ARM02", "OFFLINE-TARGET"):
            device = Device(device_code=code, device_name="offline explicit", work_line_id=line.id)
            db.add(device)
            await db.flush()
            live.device_contracts = {
                **live.device_contracts,
                code: {
                    "device_id": device.id,
                    "endpoint_base_url": "http://127.0.0.1:1",
                    "contract_key": "FIXTURE_ONLY",
                    "contract_version": "1",
                    "command_timeout_ms": 30000,
                    "status_max_age_ms": 30000,
                },
            }
        live.config = {"device_bindings": {"TARGET_ARM": "ARM02", "FIXTURE_TARGET": "OFFLINE-TARGET"}}
        await db.flush()
        p.member = member
    return p


async def application(p):
    commands = DeviceCommandService(session_factory=p.sessions, clock=lambda: p.now)
    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    base = build_transport_plugin(
        session_factory=p.sessions,
        transport_service=service,
        commands=commands,
        workline_owner=RackDepartureOwnerService(),
        task_queue_gateway=None,
    )

    async def direct_request(db, *, owner, member, source, pickup_id):
        # 仅测试参数，不能作为 ARM02 未冻结定位协议的默认映射。
        return DeviceCommandRequest(
            device_code="ARM02",
            workline_id=owner.workline_id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=pickup_id,
            material_execution_id=None,
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            task_type="FIXTURE_PICK",
            params={"fixture_only": True, "source": asdict(source)},
            deadline_at=p.now + timedelta(seconds=10),
        )

    class Absent:
        async def read_in_session(self, *args, **kwargs):
            return None

    async def no_bin_request(*args, **kwargs):
        raise AssertionError("DirectPick must not create a Bin Work")

    return build_picking_plugin(
        transport_plugin=base,
        commands=commands,
        physical_reader=p.observations,
        arm_commands=N(source_request=no_bin_request, put_request=lambda db, **kw: request(p, db, **kw)),
        scan_reader=FixtureScanReader(),
        target_reader=FixtureTargetReader(p),
        put_reader=FixturePutReader(p),
        rack_transports=ReliableRackTransportCreator(service, inbound_window=RackInboundWindowService()),
        rotation_reader=Absent(),
        departure_reader=Absent(),
        direct_pick_requests=direct_request,
    )


async def start(p, plugin):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        await plugin.picking_task_batch_driver.advance_in_session(db, line, p.task)
    async with p.sessions() as db:
        p.pickup_id = await db.scalar(select(AutomaticPickingPickup.pickup_id))
        assert p.pickup_id is not None
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0


async def test_direct_member_authorization_through_report_close_without_work(database):
    p = await prepared_direct(database)
    plugin = await application(p)
    await start(p, plugin)
    p.scan_id = await scan(p, p.pickup_id)
    await processor(p, plugin).process_batch()
    p.now += timedelta(seconds=2)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        p.material_id = (await current_pickup(p, db)).material_execution_id
        decide = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))
        assert decide.request_payload["data"]["plan_revision"] == 1
        assert decide.request_payload["data"]["source_locator"] == {
            "type": "RACK_SLOT",
            "rack_id": "RETURN-RACK",
            "rack_face": "F2",
            "slot_id": "REEL-SLOT",
        }
    await result(p, decide, data={"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}})
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
    # 明确原取盘成功及归属；不把扫码或 ACK 当作可靠取出。
    from test_rotated_target_put_postgresql import removed

    await removed(p)
    await tick(p, plugin)
    await successful_put(p)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        report = await db.get(WmsConfirmation, pickup.movement_confirmation_id)
        assert report.request_payload["data"]["barcode"] == BARCODE
        assert report.request_payload["data"]["plan_revision"] == 1
        assert (await db.get(MaterialExecution, p.material_id)).status != "CLOSED"
    await report_result(p, "RECORDED")
    from unittest.mock import patch

    app = plugin.runtime_binding.fact_factory
    original_apply = app.put_flow.apply_in_session

    async def crash_after_close_reference(*args, **kwargs):
        await original_apply(*args, **kwargs)
        raise RuntimeError("crash after member completion")

    with patch.object(app.put_flow, "apply_in_session", crash_after_close_reference):
        await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status != "CLOSED"
        assert (await db.scalar(select(AutomaticPickingDirectPick))).completed_evidence_id is None
    p.now += timedelta(seconds=2)
    await processor(p, await application(p)).process_batch()
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0
        owner = await db.scalar(select(AutomaticPickingDirectPick))
        assert owner.completed_evidence_id == p.report_result_id
    from src.app.device.models.command import DeviceCommand

    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        codes = [pickup.command_code, pickup.put_command_code]
        await db.delete(pickup)
        await db.flush()
        await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code.in_(codes)))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == p.put_evidence_id))
    # 完成事实归当前成员，恢复和新业务准入均不依赖已闭合 Pickup/Command/报告历史。
    rebuilt = await application(p)
    await tick(p, rebuilt)
    async with p.sessions.begin() as db:
        await rebuilt.picking_task_batch_driver.advance_in_session(db, p.line, p.task)
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 0
        assert (await db.scalar(select(AutomaticPickingDirectPick))).completed_evidence_id == p.report_result_id


async def admitted(p, plugin):
    await start(p, plugin)
    p.scan_id = await scan(p, p.pickup_id)
    await processor(p, plugin).process_batch()
    p.now += timedelta(seconds=2)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        p.material_id = (await current_pickup(p, db)).material_execution_id
        return await db.scalar(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))


async def test_direct_current_command_is_atomic_concurrent_and_survives_restart_without_executing(
    database, monkeypatch
):
    from src.app.device.models.command import DeviceCommand
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = await prepared_direct(database)
    plugin = await application(p)
    flow = plugin.picking_task_batch_driver.direct_pick_flow
    original = flow.commands.create_command_in_session

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("crash after command")

    with monkeypatch.context() as patch:
        patch.setattr(flow.commands, "create_command_in_session", crash)
        with pytest.raises(RuntimeError, match="crash after command"):
            await start(p, plugin)
    async with p.sessions() as db:
        for model in (DeviceCommand, AutomaticPickingPickup, AutomaticPickingDirectPick):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
    await asyncio.wait_for(asyncio.gather(*(start(p, plugin) for _ in range(3))), timeout=15)
    from automatic_picking.application.work_repository import WorkRepository

    async with p.sessions() as db:
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == 1
    original_id = p.pickup_id
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    await tick(p, await application(p))
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.pickup_id == original_id and pickup.direct_pick_id is not None and pickup.work_id is None
        command = await db.scalar(select(DeviceCommand))
        assert command.device_code == "ARM02" and command.execution_ref_id == original_id
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


async def test_direct_wait_keeps_revision_and_original_scan_after_restart_without_executing(database):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = await prepared_direct(database)
    plugin = await application(p)
    decide = await admitted(p, plugin)
    await result(p, decide, data={"result": "WAIT", "retry_after_ms": 1000})
    await processor(p, plugin).process_batch()
    async with p.sessions.begin() as db:
        owner = await current_pickup(p, db)
        due = owner.material_retry_at
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    p.now = due - timedelta(milliseconds=1)
    await tick(p, await application(p))
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).material_confirmation_id == decide.id
    p.now = due
    plugin = await application(p)
    await asyncio.wait_for(asyncio.gather(*(tick(p, plugin) for _ in range(3))), timeout=15)
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        successor = await db.get(WmsConfirmation, pickup.material_confirmation_id)
        assert successor.id != decide.id
        assert successor.request_payload["data"] == decide.request_payload["data"]
        assert successor.request_payload["data"]["plan_revision"] == p.member.plan_revision
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2
    # 旧结果重放不得改写当前请求；新决定继续使用同一 Pickup。
    await result(p, successor, data={"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}})
    await processor(p, plugin).process_batch()
    await tick(p, await application(p))
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).material_confirmation_id == successor.id


async def test_cancelled_member_or_missing_arrival_never_starts_and_new_revision_is_independent(database):
    p = await prepared_direct(database)
    plugin = await application(p)
    p.observations.ready = False
    async with p.sessions.begin() as db:
        await plugin.picking_task_batch_driver.advance_in_session(db, p.line, p.task)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 0
    async with p.sessions.begin() as db:
        member = await db.get(DirectPickExecution, p.member.id)
        member.cancelled_evidence_id = p.task.issued_evidence_id
        db.add(
            DirectPickExecution(
                picking_task_id=p.task.id,
                rack_id=member.rack_id,
                rack_face=member.rack_face,
                slot_id=member.slot_id,
                plan_revision=2,
                source_evidence_id=p.task.issued_evidence_id,
            )
        )
    p.observations.ready = True
    await start(p, plugin)
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        owner = await db.get(AutomaticPickingDirectPick, pickup.direct_pick_id)
        assert owner.member_id != p.member.id
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 1


async def test_scan_must_keep_exact_pickup_and_command_and_accept_has_no_work_successor(database):
    p = await prepared_direct(database)
    plugin = await application(p)
    await start(p, plugin)
    bad = await scan(p, p.pickup_id, command_code="ANOTHER-COMMAND")
    with pytest.raises(ValueError, match="identity"):
        async with p.sessions.begin() as db:
            await plugin.runtime_binding.business_evidence_consumer.material_flow.bind_scan_in_session(
                db, evidence_id=bad
            )
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
    # 不处理该错误输入；持久记录仍保留，正常扫码由新的精确 Evidence 驱动。
    async with p.sessions.begin() as db:
        row = await db.get(InboundEvidence, bad)
        row.published_at = p.now
        row.decision_digest = "a" * 64
    decide = await admitted_existing(p, plugin)
    from test_pickup_identity_postgresql import OTHER

    await result(
        p,
        decide,
        data={
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)},
            "next_source_action": asdict(OTHER),
        },
    )
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).accept_evidence_id is None
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0


async def admitted_existing(p, plugin):
    p.scan_id = await scan(p, p.pickup_id)
    await processor(p, plugin).process_batch()
    p.now += timedelta(seconds=2)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        p.material_id = (await current_pickup(p, db)).material_execution_id
        return await db.scalar(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))


async def test_new_schema_round_trip_and_protects_current_progress(database):
    import subprocess

    from sqlalchemy import text

    from tests.support.postgresql_heavy import run_alembic

    url, sessions = database
    run_alembic("downgrade", "8c9d01122445", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with sessions() as db:
        assert await db.scalar(text("SELECT to_regclass('wes_biz.automatic_picking_direct_picks')")) is not None
    p = await prepared_direct(database)
    await start(p, await application(p))
    with pytest.raises(subprocess.CalledProcessError) as exc:
        run_alembic("downgrade", "8c9d01122445", database_url=url)
    assert "downgrade refused" in exc.value.stderr


@pytest.mark.parametrize("mode", ["ROTATE", "REPLACE"])
async def test_direct_target_preparation_never_invents_missing_transport_inputs(database, mode):
    p = await prepared_direct(database)
    plugin = await application(p)
    decide = await admitted(p, plugin)
    await result(
        p,
        decide,
        data={
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)},
            "target_preparation": {"mode": mode},
        },
    )
    await processor(p, plugin).process_batch()
    from test_rotated_target_put_postgresql import removed

    await removed(p)
    await tick(p, plugin)
    from src.app.transport.models import TransportTask

    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.put_command_code is None and pickup.accept_evidence_id is not None
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 0
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0
