"""料箱 Work READY 后正常循环的统一离线装配；设备参数/关联 显式注入，不派发。"""

from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
from sqlalchemy import delete, select
from test_material_close_postgresql import report_result
from test_material_flow_postgresql import BARCODE, FixtureScanReader, result, scan
from test_pickup_identity_postgresql import TARGET, components, database, prepared
from test_placement_report_postgresql import FixturePutReader, successful_put
from test_rotated_target_put_postgresql import removed, target
from test_target_put_postgresql import FixtureTargetReader, current_pickup, request
from test_target_replacement_departure_postgresql import first_departure, replacing
from test_target_replacement_inbound_postgresql import incoming
from test_target_rotation_postgresql import rotating

from src.app.device.models.device import Device
from src.app.execution.models import MaterialExecution, WmsConfirmation
from src.app.execution.plugin_binding import StaticPluginBinding
from src.app.execution.services import DecisionApplier, FactProcessor, MaterialExecutionService, WmsConfirmationService
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def application(p):
    from automatic_picking.application.feed_model import AutomaticPickingFeed
    from automatic_picking.application.transport_composition import build_picking_plugin, build_transport_plugin

    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator

    _repo, commands, source = components(p)
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        from automatic_picking.definition import DEFINITION

        line.position_bindings = {
            **{
                slot.slot_key: {"location_type": slot.location_type, "location_id": "NORMAL-" + slot.slot_key}
                for slot in DEFINITION.position_slots
            },
            **line.position_bindings,
        }
        # Work READY 已是当前前提；上游投料仍保留原成员关联，不重复投料矩阵。
        (await db.get(AutomaticPickingFeed, p.work.feed_id)).archived_at = p.now
        for code in ["OFFLINE-TARGET", "OFFLINE-SCAN2", "OFFLINE-SCAN3"]:
            device = await db.scalar(select(Device).where(Device.device_code == code))
            if device is None:
                device = Device(device_code=code, device_name="normal offline", work_line_id=line.id)
                db.add(device)
                await db.flush()
            line.device_contracts = {
                **line.device_contracts,
                code: {
                    "device_id": device.id,
                    "endpoint_base_url": "http://127.0.0.1:1",
                    "contract_key": "FIXTURE_ONLY",
                    "contract_version": "1",
                    "command_timeout_ms": 30000,
                    "status_max_age_ms": 30000,
                },
            }
        line.config = {
            "device_bindings": {
                **{role.role_key: role.role_key for role in DEFINITION.device_roles},
                "SOURCE_ARM": "OFFLINE-SOURCE",
                "TARGET_ARM": "OFFLINE-TARGET",
                "SCAN2": "OFFLINE-SCAN2",
                "SCAN3": "OFFLINE-SCAN3",
            }
        }

    p.target_ready, p.request_changes, p.identity_available, p.observation_changes = True, {}, True, {}

    class ScanReader:
        async def read_in_session(self, db, *, evidence_id):
            from src.app.execution.models import InboundEvidence

            row = await db.get(InboundEvidence, evidence_id)
            return (
                await FixtureScanReader().read_in_session(db, evidence_id=evidence_id)
                if "fixture" in row.normalized_payload and row.kind == "DEVICE_EVENT"
                else None
            )

    class MissingPreparation:
        async def read_in_session(self, *args, **kwargs):
            return None

    service = getattr(p, "service", None) or TransportService(
        p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420)
    )
    base = build_transport_plugin(
        session_factory=p.sessions,
        transport_service=service,
        commands=commands,
        workline_owner=RackDepartureOwnerService(),
        task_queue_gateway=None,
    )
    base.picking_task_batch_driver.exit_flow.request_mapper.__self__.clock = lambda: p.now
    p.picking_kwargs = {
        "transport_plugin": base,
        "commands": commands,
        "physical_reader": p.observations,
        "arm_commands": N(source_request=source.request_factory, put_request=lambda db, **kw: request(p, db, **kw)),
        "scan_reader": ScanReader(),
        "target_reader": FixtureTargetReader(p),
        "put_reader": FixturePutReader(p),
        "rack_transports": ReliableRackTransportCreator(service, inbound_window=RackInboundWindowService()),
        "rotation_reader": MissingPreparation(),
        "departure_reader": MissingPreparation(),
    }
    plugin = build_picking_plugin(**p.picking_kwargs)
    assert base.picking_task_batch_driver.source_flow is None
    assert base.runtime_binding.business_evidence_consumer.material_flow is None
    return plugin


def processor(p, plugin):
    return FactProcessor(
        session_factory=p.sessions,
        plugin_binding=StaticPluginBinding((plugin.runtime_binding,)),
        decision_applier=DecisionApplier(
            device_command_service=components(p)[1],
            wms_confirmation_service=WmsConfirmationService(),
            transport_service=N(),
            material_execution_service=MaterialExecutionService(),
            clock=lambda: p.now,
        ),
        clock=lambda: p.now,
    )


async def tick(p, plugin):
    plugin.picking_task_batch_driver.clock = lambda: p.now
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        return await plugin.picking_task_batch_driver.advance_completed_in_session(db, line)


@pytest.mark.parametrize("with_next", [False, True])
async def test_work_ready_through_scan_decide_put_report_close_and_normal_exit(prepared, with_next):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup
    from automatic_picking.application.work_model import AutomaticPickingWork
    from test_pickup_identity_postgresql import OTHER

    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    p = prepared
    plugin = await application(p)
    await tick(p, plugin)
    for number in range(2 if with_next else 1):
        async with p.sessions() as db:
            work = await db.get(AutomaticPickingWork, p.work.id)
            p.pickup_id = work.consumed_pickup_id
            assert p.pickup_id is not None
        p.scan_id = await scan(p, p.pickup_id)
        await processor(p, plugin).process_batch()
        p.now += timedelta(seconds=2)
        await processor(p, plugin).process_batch()
        async with p.sessions() as db:
            p.material_id = (await current_pickup(p, db)).material_execution_id
            decide = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id)
            )
            assert decide is not None
        if with_next and number == 0:
            await result(p, decide, data={"result": "WAIT", "retry_after_ms": 500})
            await processor(p, plugin).process_batch()
            await tick(p, plugin)
            async with p.sessions() as db:
                assert (await current_pickup(p, db)).material_confirmation_id == decide.id
            p.now += timedelta(seconds=1)
            await tick(p, plugin)
            async with p.sessions() as db:
                decide = await db.get(WmsConfirmation, (await current_pickup(p, db)).material_confirmation_id)
        data = {"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}}
        if with_next and number == 0:
            data["next_source_action"] = asdict(OTHER)
        await result(p, decide, data=data)
        await processor(p, plugin).process_batch()
        await tick(p, plugin)
        async with p.sessions() as db:
            assert (await current_pickup(p, db)).put_command_code is None
        await removed(p)
        await tick(p, plugin)
        async with p.sessions() as db:
            work = await db.get(AutomaticPickingWork, p.work.id)
            if with_next and number == 0:
                assert work.consumed_pickup_id != p.pickup_id
            assert work.phase == "READY" and work.exit_command_code is None
        await successful_put(p)
        await processor(p, plugin).process_batch()
        await tick(p, plugin)
        async with p.sessions() as db:
            assert (await db.get(AutomaticPickingWork, p.work.id)).exit_command_code is None
        await report_result(p, "RECORDED" if number == 0 else "DUPLICATE")
        await processor(p, plugin).process_batch()
        async with p.sessions() as db:
            assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        if with_next and number == 0:
            async with p.sessions.begin() as db:
                await db.execute(delete(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == p.pickup_id))
            from automatic_picking.application.transport_composition import build_picking_plugin

            plugin = build_picking_plugin(**p.picking_kwargs)
    async with p.sessions() as db:
        reports = (
            await db.scalars(
                select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.material.movement_report@v1")
            )
        ).all()
        assert len(reports) == (2 if with_next else 1)
        assert all(report.request_payload["data"]["barcode"] == BARCODE for report in reports)
        assert len({report.material_execution_id for report in reports}) == len(reports)
    exit_flow = plugin.picking_task_batch_driver.exit_flow
    original = exit_flow.commands

    class CrashAfterCreate:
        async def create_command_in_session(self, *args, **kwargs):
            await original.create_command_in_session(*args, **kwargs)
            raise RuntimeError("offline exit crash")

    exit_flow.commands = CrashAfterCreate()
    with pytest.raises(RuntimeError, match="exit crash"):
        await tick(p, plugin)
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.phase == "READY" and work.exit_command_code is None
    exit_flow.commands = original
    await tick(p, plugin)
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.phase == "EXIT_PENDING" and work.exit_command_code is not None
        original_exit = work.exit_command_code
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).exit_command_code == original_exit
    from automatic_picking.application.return_model import AutomaticPickingReturn
    from test_transport_composition_postgresql import event

    first = await event(p, "SCAN3", 1000)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).phase == "CLOSED"
        returned = await db.scalar(
            select(AutomaticPickingReturn).where(AutomaticPickingReturn.source_work_id == p.work.id)
        )
        assert returned.scan3_evidence_id == first
    assert await processor(p, plugin).process_batch() == 0


@pytest.mark.parametrize("crash", [False, True])
async def test_replace_put_report_close_and_next_source_share_one_application(incoming, crash):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup
    from test_target_replacement_departure_postgresql import callback
    from test_target_replacement_inbound_postgresql import inbound

    p = incoming
    plugin = await application(p)
    await tick(p, plugin)
    task = await inbound(p)
    await removed(p)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
    publisher, scan_flow = await callback(p, task, publish=False)
    scan_flow.batch_driver.target_flow = plugin.picking_task_batch_driver.target_flow
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
    await processor(p, plugin).process_batch()
    target_flow = plugin.picking_task_batch_driver.target_flow
    original_commands = target_flow.commands
    if crash:

        class CrashPut:
            async def create_command_in_session(self, *args, **kwargs):
                await original_commands.create_command_in_session(*args, **kwargs)
                raise RuntimeError("offline PUT crash")

        target_flow.commands = CrashPut()
        with pytest.raises(RuntimeError, match="PUT crash"):
            await tick(p, plugin)
        async with p.sessions() as db:
            assert (await current_pickup(p, db)).put_command_code is None
        target_flow.commands = original_commands
    await tick(p, plugin)
    async with p.sessions() as db:
        m1 = await current_pickup(p, db)
        assert m1.put_command_code is not None
        m2 = await db.scalar(
            select(AutomaticPickingPickup).where(
                AutomaticPickingPickup.work_id == p.work.id, AutomaticPickingPickup.pickup_id != p.pickup_id
            )
        )
        assert m2 is not None  # 无需等待M1 PUT/report完成。
        original = m1.put_command_code

    async def unavailable(*args, **kwargs):
        raise AssertionError("original PUT must not reenter preparation")

    target_flow.replacement_flow.prepare_in_session = unavailable
    target_flow.replacement_flow.read_in_session = unavailable
    target_flow.request_factory = unavailable
    await tick(p, plugin)
    await successful_put(p)
    if crash:
        p.identity_available = False
        await processor(p, plugin).process_batch()
        async with p.sessions() as db:
            assert (await current_pickup(p, db)).movement_confirmation_id is None
        p.identity_available = True
        p.now += timedelta(seconds=2)
    await processor(p, plugin).process_batch()
    await report_result(p, "DUPLICATE")
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        assert (await current_pickup(p, db)).put_command_code == original


async def test_rotate_branch_uses_the_same_put_report_close_pipeline(target):
    from automatic_picking.application.transport_composition import build_picking_plugin
    from test_target_rotation_postgresql import application as rotation_application
    from test_target_rotation_postgresql import outcome

    p = target
    plugin = await application(p)
    p.picking_kwargs["rotation_reader"] = rotation_application(p).position_reader
    plugin = build_picking_plugin(**p.picking_kwargs)
    await tick(p, plugin)
    service, publisher, scan_flow = await outcome(p, publish=False)
    scan_flow.batch_driver.target_flow = plugin.picking_task_batch_driver.target_flow
    await service.publish_pending_outcomes(100, publisher)
    await processor(p, plugin).process_batch()
    await removed(p)
    await tick(p, plugin)
    await successful_put(p)
    await processor(p, plugin).process_batch()
    await report_result(p)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"


async def test_replace_first_decision_wait_through_original_f01_put_report_close(first_departure):
    from automatic_picking.application.transport_composition import build_picking_plugin
    from test_target_replacement_departure_postgresql import (
        LOCATION,
        callback,
        current_request,
        decision_application,
        departure,
        departure_response,
    )
    from test_target_replacement_inbound_postgresql import inbound

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding
    from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
    from src.app.transport.models import TransportMember, TransportTask
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = first_departure
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        db.add(
            WorkLinePosition(
                workline_id=p.line.id,
                workline_code=p.line.line_code,
                position_code=LOCATION,
                position_type="RACK_POSITION",
                position_name="offline transfer",
                position_role="SMT_TRANSFER_RACK_POSITION",
                allowed_rack_kind="FIVE_LAYER",
                logic_location_code=LOCATION,
                capacity=1,
                enabled=True,
            )
        )
    await application(p)
    p.picking_kwargs["departure_reader"] = decision_application(p).replacement_flow.reader
    plugin = build_picking_plugin(**p.picking_kwargs)
    plugin.picking_task_batch_driver.target_flow.replacement_flow.clock = lambda: p.now
    await removed(p)
    await tick(p, plugin)
    first = await current_request(p)
    assert first is not None and await departure(p) is None
    wait_id = await departure_response(p, first)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, wait_id)).published_at is not None
    p.now += timedelta(milliseconds=50)
    await tick(p, plugin)
    second = await current_request(p)
    assert second.operation_id != first.operation_id
    ready_id = await departure_response(p, second, "READY")
    await tick(p, plugin)
    assert await departure(p) is None
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    outbound = await departure(p)
    assert outbound is not None
    async with p.sessions() as db:
        binding = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == outbound.client_request_id
            )
        )
        assert binding.source_evidence_id == ready_id
    publisher, scan_flow = await callback(p, outbound, publish=False)
    scan_flow.batch_driver.target_flow = plugin.picking_task_batch_driver.target_flow
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    new_target = await inbound(p)
    assert new_target is not None
    p.observed = None
    async with p.sessions.begin() as db:
        original = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == p.inbound.client_request_id
            )
        )
        await db.execute(
            delete(TransportMember).where(TransportMember.transport_task_id == p.inbound.transport_task_id)
        )
        await db.execute(delete(TransportTask).where(TransportTask.id == p.inbound.id))
        await db.delete(original)
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == p.arrival_id))
        await db.delete(await db.get(WmsConfirmation, first.id))
        await db.delete(await db.get(InboundEvidence, wait_id))
    plugin = build_picking_plugin(**p.picking_kwargs)  # 新实例不需要已清理的原进场或旧WAIT。
    publisher, scan_flow = await callback(p, new_target, publish=False)
    scan_flow.batch_driver.target_flow = plugin.picking_task_batch_driver.target_flow
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    await successful_put(p)
    await processor(p, plugin).process_batch()
    await report_result(p, "RECORDED")
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        original_commands = pickup.command_code, pickup.put_command_code
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        assert pickup.target_departure_confirmation_id == second.id
        assert pickup.target_departure_transport_task_id == outbound.transport_task_id
        assert pickup.target_inbound_transport_task_id == new_target.transport_task_id
    await tick(p, plugin)
    assert await processor(p, plugin).process_batch() == 0
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert (pickup.command_code, pickup.put_command_code) == original_commands
