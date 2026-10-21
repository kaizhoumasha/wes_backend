"""真实统一设备入口＋正常插件装配；供应商参数/扫码关联/PkgID 显式离线输入。"""

import asyncio
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
from automatic_picking.application.picking_device_ports import (
    PickingArmCommandMapper,
    PickingDepartureReader,
    PickingMaterialScanReader,
    PickingPutReader,
    PickingSourceReader,
    PickingTargetReader,
)
from automatic_picking.application.transport_composition import build_picking_plugin
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import func, select
from test_material_close_postgresql import report_result
from test_material_flow_postgresql import BARCODE, result
from test_normal_picking_postgresql import application, processor, tick
from test_pickup_identity_postgresql import SOURCE, TARGET, components, database, prepared
from test_target_put_postgresql import current_pickup
from test_target_replacement_departure_postgresql import callback, first_departure, replacing
from test_target_replacement_inbound_postgresql import inbound, incoming
from wes_plugin_sdk import TransportRackPosition, TransportRackReference, TransportRcsTemplateId

from src.app.device.contracts import EcsCommandResultReport, EcsDeviceEvent, EcsDeviceEventReport
from src.app.device.models.command import DeviceCommand
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.execution.models import InboundEvidence, MaterialExecution, TransportDecisionBinding, WmsConfirmation
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def assembled(p):
    window = RackInboundWindowService()
    p.service = TransportService(
        p.sessions,
        TransportRepository(),
        N(),
        result_timeout=timedelta(seconds=420),
        progress_hook=window.on_transport_progress,
    )
    p.creator = ReliableRackTransportCreator(p.service, inbound_window=window)
    await application(p)
    p.params_available, p.pkg_id, p.associations, p.observed = True, None, {}, None

    class Parameters:
        async def source_in_session(self, db, **kwargs):
            return (
                {
                    "source": {
                        "location_id": "EXPLICIT-WORK",
                        "location_type": "BIN",
                        "bin_cell_location": "EXPLICIT-CELL",
                    },
                    "target": {"location_id": "EXPLICIT-PLATFORM", "location_type": "SCAN_PLATFORM"},
                }
                if p.params_available
                else None
            )

        async def put_in_session(self, db, **kwargs):
            return (
                {
                    "source": {"location_id": "EXPLICIT-PLATFORM", "location_type": "SCAN_PLATFORM"},
                    "target": {"location_id": "EXPLICIT-TARGET", "location_type": "FIVE_LAYER_RACK"},
                }
                if p.params_available
                else None
            )

    class SourceCommands:
        async def read_in_session(self, db, *, evidence_id):
            return p.associations.get(evidence_id)

    class PkgIds:
        async def read_in_session(self, db, **kwargs):
            assert kwargs == {
                "workline_id": p.line.id,
                "material_execution_id": p.material_id,
                "scan_evidence_id": p.scan_id,
            }
            return p.pkg_id

    mapper = PickingArmCommandMapper(Parameters(), clock=lambda: p.now)
    p.picking_kwargs.update(
        physical_reader=PickingSourceReader(),
        source_request_factory=mapper.source_request,
        scan_reader=PickingMaterialScanReader(SourceCommands()),
        target_reader=PickingTargetReader(),
        target_request_factory=mapper.put_request,
        put_reader=PickingPutReader(PkgIds()),
        departure_reader=PickingDepartureReader(),
    )
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.device_contracts = {
            code: {**binding, "contract_key": "third_party_integration", "contract_version": "1.1"}
            for code, binding in line.device_contracts.items()
        }
        fact = await db.get(InboundEvidence, p.work.scan2_evidence_id)
        event = EcsDeviceEvent(
            device_code="OFFLINE-SCAN2",
            contract_key="third_party_integration",
            contract_version="1.1",
            event_type="SCAN_COMPLETED",
            timestamp=p.work.scanned_at,
            source_event_id=fact.source_identity,
            is_debug=False,
            data={"bin_code": p.work.bin_code + "-C"},
        )
        fact.normalized_payload, fact.payload_digest = normalize_payload(
            event.model_dump(mode="json", exclude_unset=True), digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE
        )
        fact.device_code, fact.published_at = event.device_code, p.now
        fact.contract_key, fact.contract_version = event.contract_key, event.contract_version
    return build_picking_plugin(**p.picking_kwargs)


async def arrived(p, rack=TARGET.rack_id, face=TARGET.rack_face):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        location = line.position_bindings["TRANSFER_RACK"]["location_id"]
        handle = await p.creator.create(
            db,
            workline_id=line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.task.issued_evidence_id,
            correlation_id="offline-arm-target:" + new_uuid7(),
            step="PICKING_TASK_TARGET_RACK_IN",
            resource_fence_id=rack,
            intent=N(
                rack_id=rack,
                source=TransportRackReference(rack),
                target=TransportRackPosition(location),
                target_face=face,
                rcs_template_id=TransportRcsTemplateId.F01,
            ),
        )
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == task.client_request_id)
        )
        binding.window_target_location_code = location
    await callback(p, task)
    return task


async def device_result(p, code, *, quantity=1):
    ingress = DeviceEvidenceService(session_factory=p.sessions)
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        # 只建立已下发前提；不运行派发或 HTTP。特意不伪造 ACK。
        command.status = "DISPATCHING"
        device = command.device_code
    report = EcsCommandResultReport(
        command_code=code,
        device_code=device,
        result="SUCCESS",
        finish_time=456,
        data={"pick_and_put_result": "PUT_FINISHED", "actual_qty": quantity},
    )
    receipt = await ingress.accept_result(report)
    assert await ingress.process_one()
    return receipt, report


async def material_scan(p, code):
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        if command.status == "PENDING":
            command.status = "DISPATCHING"
    ingress = DeviceEvidenceService(session_factory=p.sessions)
    receipt = await ingress.accept_event(
        EcsDeviceEventReport(
            device_code="OFFLINE-SCAN2",
            event_type="SCAN_COMPLETED",
            timestamp=457,
            data={"location": "EXPLICIT-PLATFORM", "barcode": BARCODE},
        )
    )
    assert await ingress.process_one()
    p.scan_id = receipt.evidence_id
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, p.scan_id)).command_code is None
    p.associations[p.scan_id] = code


async def accepted(p, plugin, *, replace_target=False):
    await processor(p, plugin).process_batch()
    p.now += timedelta(seconds=2)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        p.material_id = pickup.material_execution_id
        decide = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))
        assert decide is not None
        assert (await db.get(InboundEvidence, p.scan_id)).command_code == pickup.command_code
    data = {"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}}
    if replace_target:
        data["target_preparation"] = {"mode": "REPLACE"}
    await result(p, decide, data=data)
    await processor(p, plugin).process_batch()


async def test_real_device_ports_complete_normal_chain_without_dispatch(prepared):
    p = prepared
    plugin = await assembled(p)
    p.params_available = False
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id is None
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 0
    p.params_available = True
    await asyncio.gather(*(tick(p, plugin) for _ in range(3)))
    async with p.sessions() as db:
        p.pickup_id = (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id
        source_code = (await current_pickup(p, db)).command_code
        source = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == source_code))
        assert source.task_type == "PICK_AND_PUT" and source.device_code == "OFFLINE-SOURCE"
    await material_scan(p, source_code)
    await accepted(p, plugin)
    await arrived(p)
    async with p.sessions.begin() as db:
        from src.app.execution.models import PositionProjection

        reader = PickingTargetReader()
        args = {"workline_id": p.line.id, "material_execution_id": p.material_id, "target": TARGET}
        assert await reader.read_in_session(db, **args) is not None
        projection = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_id == TARGET.rack_id, PositionProjection.object_type == "RACK"
            )
        )
        original_face = projection.arrival_face
        projection.arrival_face = "B"
        await db.flush()
        assert await reader.read_in_session(db, **args) is None
        projection.arrival_face = original_face
        projection.position_unknown = True
        await db.flush()
        assert await reader.read_in_session(db, **args) is None
        await db.rollback()
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
    receipt, report = await device_result(p, source_code)
    async with p.sessions() as db:
        observation = await PickingSourceReader().read_in_session(
            db,
            work=await db.get(AutomaticPickingWork, p.work.id),
            source=SOURCE,
            pickup=await current_pickup(p, db),
        )
        assert observation.removed_evidence_id is None  # 宿主应用完成，尚未发布。
    await processor(p, plugin).process_batch()
    await asyncio.gather(*(tick(p, plugin) for _ in range(3)))
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, receipt.evidence_id)).published_at is not None
        put_code = (await current_pickup(p, db)).put_command_code
        put = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == put_code))
        assert put.device_code == "OFFLINE-TARGET" and put.task_type == "PICK_AND_PUT"
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2
    p.put_evidence_id = (await device_result(p, put_code))[0].evidence_id
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).movement_confirmation_id is None
        assert (await db.get(InboundEvidence, p.put_evidence_id)).published_at is None
    p.pkg_id = "EXPLICIT-OFFLINE-PKG"
    p.now += timedelta(seconds=2)
    plugin = build_picking_plugin(**p.picking_kwargs)
    await processor(p, plugin).process_batch()
    await report_result(p, "RECORDED")
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        original_put = (await current_pickup(p, db)).put_command_code
    assert (await DeviceEvidenceService(session_factory=p.sessions).accept_result(report)).duplicate
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code == original_put


@pytest.mark.parametrize("quantity", [0, True, "1"])
async def test_arm_success_without_exact_single_put_fact_does_not_authorize_removal(prepared, quantity):
    p = prepared
    plugin = await assembled(p)
    await tick(p, plugin)
    async with p.sessions() as db:
        p.pickup_id = (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id
        code = (await current_pickup(p, db)).command_code
    await device_result(p, code, quantity=quantity)
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        observed = await PickingSourceReader().read_in_session(
            db,
            work=await db.get(AutomaticPickingWork, p.work.id),
            source=SOURCE,
            pickup=await current_pickup(p, db),
        )
        assert observed.removed_evidence_id is None


async def test_explicit_scan_correlation_is_validated_and_committed_with_material(prepared):
    p = prepared
    plugin = await assembled(p)
    await tick(p, plugin)
    async with p.sessions() as db:
        p.pickup_id = (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id
        code = (await current_pickup(p, db)).command_code
    await material_scan(p, code)
    original = p.associations[p.scan_id]
    material = plugin.runtime_binding.business_evidence_consumer.material_flow
    p.associations[p.scan_id] = "WRONG-ORIGINAL-COMMAND"
    with pytest.raises(ValueError, match="identity"):
        async with p.sessions.begin() as db:
            await material.bind_scan_in_session(db, evidence_id=p.scan_id)
    p.associations[p.scan_id] = original
    with pytest.raises(RuntimeError, match="offline bind crash"):
        async with p.sessions.begin() as db:
            await material.bind_scan_in_session(db, evidence_id=p.scan_id)
            raise RuntimeError("offline bind crash")
    async with p.sessions() as db:
        fact = await db.get(InboundEvidence, p.scan_id)
        assert fact.command_code is None and fact.material_execution_id is None
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
    await accepted(p, plugin)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 1


async def test_real_target_reader_discovers_old_rack_and_original_round_for_replace(first_departure):
    p = first_departure
    plugin = await assembled(p)
    reader = PickingDepartureReader()
    async with p.sessions() as db:
        observed = await reader.read_in_session(
            db, workline_id=p.line.id, material_execution_id=p.material_id, target=TARGET
        )
        assert observed.old_rack_id == "OLD-TARGET"
        assert observed.inbound_client_request_id == p.inbound.client_request_id
        assert observed.departure_operation_id is None
        assert (
            await PickingTargetReader().read_in_session(
                db, workline_id=p.line.id, material_execution_id=p.material_id, target=TARGET
            )
            is None
        )
    await asyncio.gather(*(tick(p, plugin) for _ in range(3)))
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        current = await db.get(WmsConfirmation, pickup.target_departure_confirmation_id)
        assert current.request_payload["data"]["rack_id"] == "OLD-TARGET"
        assert pickup.target_departure_round_id == p.inbound.client_request_id
        assert pickup.target_departure_transport_task_id is None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WmsConfirmation)
                .where(WmsConfirmation.operation == "outbound.rack.departure_decide@v1")
            )
            == 1
        )


@pytest.mark.parametrize("after_arrival", [False, True])
async def test_real_ports_late_replace_accept_uses_existing_target_obligation(incoming, after_arrival):
    p = incoming
    plugin = await assembled(p)
    original_pickup, original_material = p.pickup_id, p.material_id
    async with p.sessions.begin() as db:
        source_code = (await current_pickup(p, db)).command_code
        (
            await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == source_code))
        ).task_type = "PICK_AND_PUT"
    await device_result(p, source_code)
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    new_inbound = await inbound(p)
    assert new_inbound is not None
    if after_arrival:
        await callback(p, new_inbound)
        await tick(p, plugin)
        async with p.sessions() as db:
            put_code = (await current_pickup(p, db)).put_command_code
        p.pkg_id = "EXPLICIT-OLD-PKG"
        await device_result(p, put_code)
        await processor(p, plugin).process_batch()
        await report_result(p)
        await processor(p, plugin).process_batch()
        async with p.sessions.begin() as db:
            from automatic_picking.application.pickup_model import AutomaticPickingPickup
            from sqlalchemy import delete

            assert (await db.get(MaterialExecution, original_material)).status == "CLOSED"
            await db.execute(delete(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == original_pickup))
    async with p.sessions() as db:
        p.pickup_id = (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id
        assert p.pickup_id != original_pickup
        source_code = (await current_pickup(p, db)).command_code
    await material_scan(p, source_code)
    await accepted(p, plugin, replace_target=True)
    await tick(p, plugin)
    if not after_arrival:
        async with p.sessions() as db:
            pickup = await current_pickup(p, db)
            assert pickup.target_departure_transport_task_id == p.outbound.transport_task_id
        await tick(p, plugin)
        async with p.sessions() as db:
            assert (await current_pickup(p, db)).target_inbound_transport_task_id == new_inbound.transport_task_id
        await callback(p, new_inbound)
    await device_result(p, source_code)
    await processor(p, plugin).process_batch()
    await tick(p, plugin)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is not None
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 3
        assert (await db.get(MaterialExecution, original_material)).workline_id == p.line.id


async def test_shared_round_rejects_released_incoming_even_without_peer_reference(incoming):
    from automatic_picking.application.target_replacement_repository import TargetReplacementRepository
    from test_target_put_postgresql import advance
    from test_target_replacement_departure_postgresql import application as replacement_application

    p = incoming
    await advance(p, replacement_application(p))
    task = await inbound(p)
    await callback(p, task)
    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        # 显式建立另一个peer已接续并释放共用incoming、此peer尚未保存关联的前提。
        pickup.target_inbound_transport_task_id = None
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == task.client_request_id)
        )
        binding.window_released_at = p.now
        await db.flush()
        assert (
            await TargetReplacementRepository().shared_target_round(
                db, p.line.id, N(pickup_id="another-current-material"), TARGET
            )
            is None
        )


async def test_raw_scan_waits_for_exact_association_pause_and_restart_without_new_owner(prepared):
    p = prepared
    plugin = await assembled(p)
    await tick(p, plugin)
    async with p.sessions() as db:
        p.pickup_id = (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id
        code = (await current_pickup(p, db)).command_code
    await material_scan(p, code)
    p.associations.clear()
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        fact = await db.get(InboundEvidence, p.scan_id)
        assert fact.command_code is None and fact.material_execution_id is None
        assert fact.published_at is None and fact.apply_status == "APPLIED"
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
    # 后续权威关联显式提供；不从条码、时间或最后一条命令反推。
    p.associations[p.scan_id] = code
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).is_active = False
    p.now += timedelta(seconds=2)
    plugin = build_picking_plugin(**p.picking_kwargs)
    await processor(p, plugin).process_batch()
    async with p.sessions.begin() as db:
        assert (await db.get(InboundEvidence, p.scan_id)).material_execution_id is None
        (await db.get(WorkLine, p.line.id)).is_active = True
    p.now += timedelta(seconds=2)
    assert sum(await asyncio.gather(*(processor(p, plugin).process_batch() for _ in range(3)))) >= 1
    async with p.sessions() as db:
        fact = await db.get(InboundEvidence, p.scan_id)
        assert fact.command_code == code and fact.material_execution_id is not None
        p.material_id = fact.material_execution_id
    # 绑定已持久化；重启丢失外部关联输入仍以原Evidence恢复首次decide。
    p.associations.clear()
    p.now += timedelta(seconds=2)
    plugin = build_picking_plugin(**p.picking_kwargs)
    await processor(p, plugin).process_batch()
    await processor(p, plugin).process_batch()
    async with p.sessions() as db:
        requests = (
            await db.scalars(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))
        ).all()
        assert len(requests) == 1
        assert requests[0].request_payload["data"]["barcode"] == BARCODE
        assert requests[0].request_payload["data"]["scanned_at"] == 457
        assert (await db.get(InboundEvidence, p.scan_id)).published_at is not None
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 1
