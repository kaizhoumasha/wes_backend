"""正常机械臂的统一合同读口；未冻结的位置编码、扫码关联由必填端口显式提供。"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext

from sqlalchemy import select
from wes_plugin_sdk import PickingMaterialAccept, PickingRackSlot

from automatic_picking.definition import DEFINITION, SCAN_PLATFORM, TRANSFER_RACK, WORK_BIN
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest, EcsCommandResult, EcsDeviceEvent
from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution, PositionProjection, TransportDecisionBinding
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.wms_integration.outbound_picking.models import DirectPickExecution
from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader
from src.app.workline.installed_plugin import parse_device_bindings
from src.app.workline.models import WorkLine
from src.app.workline.repositories.workline_repository import workline_repository
from src.utils.timezone import timezone

from .material_flow import MatchedMaterialScan, MaterialScanAssociationPending
from .pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from .pickup_repository import PickupRepository, SourceObservation
from .placement_flow import MatchedPutResult
from .return_rack_model import AutomaticPickingReturnRack
from .return_rack_repository import ReturnRackRepository
from .scan_flow import normal_bin_code
from .target_preparation import ReadyTarget
from .target_replacement import MatchedTargetDeparture
from .target_replacement_repository import TARGET_INBOUND_STEP, TargetReplacementRepository


@dataclass(frozen=True, slots=True)
class PickingArmBinding:
    """调用方显式给定的当前工作线位置；箱型编码和 rack_face 直接沿授权传递。"""

    work_location: str | None
    scan_location: str | None
    target_location: str | None

    def __post_init__(self):
        if any(
            value is not None and (type(value) is not str or not value)
            for value in (self.work_location, self.scan_location, self.target_location)
        ):
            raise ValueError("arm locations require explicit nonempty binding")


class PickingArmBindingReader:
    """读取 START 已校验冻结的位置；不从设备名、扫码内容或其它插槽猜位置。"""

    def __init__(self, *, worklines=None):
        self.worklines = worklines or workline_repository

    async def read_in_session(self, db, *, workline_id):
        line = await self.worklines.get_for_authority_update(db, workline_id, populate_existing=True)
        if (
            line is None
            or line.id != workline_id
            or not line.is_active
            or line.is_deleted
            or (line.plugin_key, line.plugin_version) != (DEFINITION.plugin_key, DEFINITION.plugin_version)
        ):
            return None
        if type(line.position_bindings) is not dict:
            raise ValueError("arm position bindings must be a frozen object")
        values = []
        for slot in (WORK_BIN, SCAN_PLATFORM, TRANSFER_RACK):
            if slot.slot_key not in line.position_bindings:
                values.append(None)
                continue
            binding = line.position_bindings[slot.slot_key]
            if (
                type(binding) is not dict
                or binding.get("location_type") != slot.location_type
                or type(binding.get("location_id")) is not str
                or not binding["location_id"].strip()
            ):
                raise ValueError(f"invalid frozen arm position: {slot.slot_key}")
            values.append(binding["location_id"])
        known = [value for value in values if value is not None]
        if len(set(known)) != len(known):
            raise ValueError("arm position bindings must identify distinct positions")
        return PickingArmBinding(*values)


def _device_number(value):
    text = format(value if isinstance(value, Decimal) else Decimal(str(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class PickingArmCommandMapper:
    """按设备说明书映射位置正文；绑定缺失时由原 Work/target owner 重扫。"""

    def __init__(self, bindings, *, worklines=None, clock=timezone.now_for_db):
        self.bindings, self.clock = bindings, clock
        self.worklines = worklines or workline_repository

    async def source_request(self, db, *, work, pickup_id):
        binding = await self._binding(db, work.workline_id)
        if binding is None or binding.work_location is None or binding.scan_location is None or work.source is None:
            return None
        source = work.source
        thickness = Decimal(str(source.reel_thickness))
        # 精度由输入位数限定，避免 0.1×3 的浮点尾差或 Decimal 默认精度截断。
        if thickness.is_zero() or source.reel_layer == 0:
            total = Decimal(0)
        else:
            with localcontext() as context:
                context.prec = max(28, len(thickness.as_tuple().digits) + len(str(source.reel_layer)))
                total = thickness * source.reel_layer
        params = {
            "source": {
                "location_id": binding.work_location,
                "location_type": "BIN",
                "bin_type": str(source.bin_type),
                "bin_cell_location": str(source.cell_index),
                "reel_layer": str(source.reel_layer),
                "reel_thickness": _device_number(source.reel_thickness),
                "reel_totalthickness": _device_number(total),
                "reel_diameter": _device_number(source.reel_diameter),
            },
            "target": {"location_id": binding.scan_location, "location_type": "SCAN_PLATFORM"},
        }
        return await self._request(db, work.workline_id, "SOURCE_ARM", pickup_id, None, params, self.clock())

    async def put_request(self, db, *, workline_id, pickup_id, material_execution_id, target, now):
        binding = await self._binding(db, workline_id)
        if binding is None or binding.scan_location is None or binding.target_location is None:
            return None
        if target.rack_layer not in range(1, 6):
            raise ValueError("target rack layer is outside hardware range 1 through 5")
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        work = await PickupRepository.owner(db, pickup) if pickup is not None else None
        command = (
            await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.command_code))
            if pickup is not None
            else None
        )
        if (
            work is None
            or work.workline_id != workline_id
            or pickup.material_execution_id != material_execution_id
            or command is None
            or command.workline_id != workline_id
            or command.task_type != "PICK_AND_PUT"
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != pickup_id
            or command.material_execution_id is not None
        ):
            raise ValueError("PUT parameters lost original Pickup/source Command identity")
        source, platform = command.params.get("source"), command.params.get("target")
        if (
            type(source) is not dict
            or type(platform) is not dict
            or source.get("location_type") != "BIN"
            or platform.get("location_type") != "SCAN_PLATFORM"
            or platform.get("location_id") != binding.scan_location
            or any(type(source.get(key)) is not str or not source[key] for key in ("reel_thickness", "reel_diameter"))
        ):
            raise ValueError("PUT requires the same platform and frozen source dimensions")
        # 当前 Work 可已授权下一盘；本盘尺寸只沿原来源 Command 冻结正文读取。
        params = {
            "source": {"location_id": binding.scan_location, "location_type": "SCAN_PLATFORM"},
            "target": {
                "location_id": binding.target_location,
                "location_type": "FIVE_LAYER_RACK",
                "rack_id": target.rack_id,
                "rack_face": target.rack_face,
                "rack_layer": str(target.rack_layer),
                "rack_column": str(target.rack_column),
                "reel_thickness": source["reel_thickness"],
                "reel_diameter": source["reel_diameter"],
            },
        }
        return await self._request(
            db, workline_id, "TARGET_ARM", "put:" + pickup_id, material_execution_id, params, now
        )

    async def _binding(self, db, line_id):
        binding = await self.bindings.read_in_session(db, workline_id=line_id)
        if binding is not None and type(binding) is not PickingArmBinding:
            raise ValueError("arm parameters require a typed explicit binding")
        return binding

    async def _request(self, db, line_id, role, ref, material_id, params, now):
        if params is None:
            return None
        if type(params) is not dict or any(
            type(params.get(key)) is not dict or not params[key] for key in ("source", "target")
        ):
            raise ValueError("PICK_AND_PUT requires explicit complete source/target parameters")
        line = await self.worklines.get_for_authority_update(db, line_id, populate_existing=True)
        if line is None or not line.is_active:
            return None
        code = parse_device_bindings(line.config, DEFINITION.device_roles)[role]
        binding = await self.worklines.get_binding_for_command_creation(db, workline_id=line_id, device_code=code)
        if binding is None:
            raise ValueError("mechanical arm lacks its current frozen device contract")
        return DeviceCommandRequest(
            device_code=code,
            workline_id=line_id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=ref,
            material_execution_id=material_id,
            contract_key=binding.contract_key,
            contract_version=binding.contract_version,
            task_type="PICK_AND_PUT",
            params=params,
            deadline_at=now + timedelta(milliseconds=binding.command_timeout_ms),
        )


async def _finished_arm(db, command):
    if (
        command is None
        or command.task_type != "PICK_AND_PUT"
        or command.status != "SUCCEEDED"
        or command.result_evidence_id is None
    ):
        return None
    evidence = await db.get(InboundEvidence, command.result_evidence_id, populate_existing=True)
    if (
        evidence is None
        or evidence.kind != "DEVICE_RESULT"
        or evidence.apply_status != "APPLIED"
        or evidence.command_code != command.command_code
        or evidence.device_code != command.device_code
        or evidence.workline_id != command.workline_id
        or evidence.contract_key != command.contract_key
        or evidence.contract_version != command.contract_version
        or normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
        != evidence.payload_digest
    ):
        return None
    result = EcsCommandResult.model_validate(evidence.normalized_payload)
    if (
        result.command_code != command.command_code
        or result.device_code != command.device_code
        or result.contract_key != command.contract_key
        or result.contract_version != command.contract_version
        or result.source_event_id != evidence.source_identity
        or result.result != "SUCCESS"
        or type(result.data.get("actual_qty")) is not int
        or result.data["actual_qty"] != 1
        or result.data.get("pick_and_put_result") != "PUT_FINISHED"
    ):
        return None
    return evidence, result


class PickingMaterialScanReader:
    """读取统一扫码合同；原来源命令由显式关联端口提供，不猜供应商回显字段。"""

    def __init__(self, source_commands=None):
        self.source_commands = source_commands

    async def read_in_session(self, db, *, evidence_id):
        evidence = await db.get(InboundEvidence, evidence_id, populate_existing=True)
        if evidence is None or evidence.kind != "DEVICE_EVENT" or evidence.apply_status != "APPLIED":
            return None
        event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
        if event.event_type != "SCAN_COMPLETED" or event.is_debug or "barcode" not in event.data:
            return None
        if (
            event.device_code != evidence.device_code
            or event.contract_key != evidence.contract_key
            or event.contract_version != evidence.contract_version
            or event.source_event_id != evidence.source_identity
            or normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            != evidence.payload_digest
            or type(event.data["barcode"]) is not str
            or not 1 <= len(event.data["barcode"]) <= 1000
            or type(event.data.get("location")) is not str
            or not event.data["location"]
        ):
            raise ValueError("material scan differs from its admitted uniform Evidence")
        # 已绑定关联属于当前 Evidence，恢复不再依赖外部关联端口或已闭合历史。
        code = evidence.command_code
        if code is None and self.source_commands is not None:
            code = await self.source_commands.read_in_session(db, evidence_id=evidence_id)
        if code is None:
            raise MaterialScanAssociationPending("material scan needs its exact original source Command")
        if type(code) is not str or not code:
            raise ValueError("material scan source Command identity is invalid")
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.command_code == code))
        work = await PickupRepository.owner(db, pickup) if pickup is not None else None
        if (
            command is None
            or pickup is None
            or work is None
            or command.task_type != "PICK_AND_PUT"
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != pickup.pickup_id
            or command.material_execution_id is not None
            or command.workline_id != work.workline_id
            or event.device_code != command.device_code
            or evidence.workline_id != work.workline_id
        ):
            raise ValueError("material scan lost original Work/Pickup/source Command identity")
        target = command.params.get("target")
        if (
            type(target) is not dict
            or target.get("location_type") != "SCAN_PLATFORM"
            or target.get("location_id") != event.data["location"]
        ):
            raise ValueError("material scan platform differs from its frozen source Command")
        if command.status == "PENDING":
            raise MaterialScanAssociationPending("material scan source Command has not been dispatched")
        return MatchedMaterialScan(
            evidence_id, work.workline_id, pickup.pickup_id, code, event.data["barcode"], event.timestamp
        )


class PickingSourceReader:
    async def read_in_session(self, db, *, work, source, pickup):
        line = await db.get(WorkLine, work.workline_id)
        ready = (
            await self._direct_ready(db, line, work, source)
            if type(work) is AutomaticPickingDirectPick
            else await self._bin_ready(db, line, work, source)
        )
        removed = None
        if pickup is not None:
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.command_code))
            if (
                command is None
                or (
                    (pickup.direct_pick_id != work.id or pickup.work_id is not None)
                    if type(work) is AutomaticPickingDirectPick
                    else (pickup.work_id != work.id or pickup.direct_pick_id is not None)
                )
                or command.workline_id != work.workline_id
                or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
                or command.execution_ref_id != pickup.pickup_id
                or command.material_execution_id is not None
            ):
                raise ValueError("source physical fact lost original Work/Pickup/Command identity")
            finished = await _finished_arm(db, command)
            removed = finished[0].id if finished is not None and finished[0].published_at is not None else None
        return SourceObservation(ready, removed)

    async def _bin_ready(self, db, line, work, source):
        scan = await db.get(InboundEvidence, work.scan2_evidence_id)
        ready = False
        if (
            line is not None
            and line.is_active
            and source is not None
            and scan is not None
            and scan.kind == "DEVICE_EVENT"
            and scan.apply_status == "APPLIED"
            and scan.published_at is not None
            and scan.workline_id == line.id
            and normalize_payload(scan.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            == scan.payload_digest
        ):
            event = EcsDeviceEvent.model_validate(scan.normalized_payload)
            code = parse_device_bindings(line.config, DEFINITION.device_roles)["SCAN2"]
            ready = (
                event.event_type == "SCAN_COMPLETED"
                and not event.is_debug
                and event.device_code == scan.device_code == code
                and event.contract_key == scan.contract_key
                and event.contract_version == scan.contract_version
                and event.source_event_id == scan.source_identity
                and event.timestamp == work.scanned_at
                and normal_bin_code(event.data.get("bin_code"), "SCAN2") == work.bin_code
            )
        return ready

    async def _direct_ready(self, db, line, owner, source):
        member = await db.get(DirectPickExecution, owner.member_id, populate_existing=True)
        if (
            member is None
            or member.picking_task_id != owner.picking_task_id
            or type(source) is not PickingRackSlot
            or source != PickingRackSlot(member.rack_id, member.rack_face, member.slot_id)
        ):
            raise ValueError("return source differs from its exact member authorization")
        if line is None or not line.is_active or member.cancelled_evidence_id is not None:
            return False
        location = line.position_bindings.get("RETURN_RACK", {}).get("location_id")
        if not location:
            return False
        repository = ReturnRackRepository()
        if await repository.member_done(db, member) or not await repository.member_admissible(db, line.id, member):
            return False
        currents = (
            await db.scalars(
                select(AutomaticPickingReturnRack).where(
                    AutomaticPickingReturnRack.workline_id == line.id,
                    AutomaticPickingReturnRack.picking_task_id == owner.picking_task_id,
                    AutomaticPickingReturnRack.rack_id == member.rack_id,
                    AutomaticPickingReturnRack.closed_evidence_id.is_(None),
                )
            )
        ).all()
        for current in currents:
            if any(
                value is not None
                for value in (
                    current.rotation_transport_task_id,
                    current.departure_confirmation_id,
                    current.departure_transport_task_id,
                )
            ):
                continue
            arrival = await repository.current_arrival(db, current)
            if arrival is not None and (
                arrival[2].arrival_face == member.rack_face
                and arrival[2].position_json == {"kind": "RACK_POSITION", "location_code": location}
            ):
                return True
        return False


class PickingTargetReader:
    """只从当前已发布的原因果到位取事实；不以计划目标或缺少历史推断在位。"""

    def __init__(self):
        self.repository = TargetReplacementRepository()
        self.materials = MaterialDecideResultReader()

    async def _material(self, db, line_id, material_id, target):
        pickup = await db.scalar(
            select(AutomaticPickingPickup).where(AutomaticPickingPickup.material_execution_id == material_id)
        )
        work = await PickupRepository.owner(db, pickup) if pickup is not None else None
        if work is None or work.workline_id != line_id:
            raise ValueError("target facts require this material's WorkLine")
        snapshot = (
            await self.materials.by_id(
                db, material_execution_id=material_id, confirmation_id=pickup.material_confirmation_id
            )
            if pickup.material_confirmation_id is not None
            else await self.materials.by_operation_id(
                db, material_execution_id=material_id, operation_id=pickup.pickup_id
            )
        )
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or snapshot.published_at is None
            or snapshot.evidence_id != pickup.accept_evidence_id
            or type(snapshot.outcome.result) is not PickingMaterialAccept
            or snapshot.outcome.result.target_locator != target
        ):
            raise ValueError("target fact input differs from this published ACCEPT")
        return pickup

    async def _current(self, db, line_id, pickup):
        line = await db.get(WorkLine, line_id)
        if line is None or not line.is_active:
            return None
        location = line.position_bindings.get("TRANSFER_RACK", {}).get("location_id")
        if location is None:
            return None
        positions = list(
            (
                await db.scalars(
                    select(PositionProjection).where(
                        PositionProjection.workline_id == line_id,
                        PositionProjection.object_type == "RACK",
                        PositionProjection.position_unknown.is_(False),
                        PositionProjection.position_json["location_code"].as_string() == location,
                    )
                )
            ).all()
        )
        if len(positions) > 1:
            raise ValueError("target working position has ambiguous authoritative racks")
        if not positions:
            return None
        position = positions[0]
        if position.source_effect_phase != "FINAL_RESULT":
            return None
        roots = list(
            (
                await db.scalars(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.workline_id == line_id,
                        TransportDecisionBinding.resource_fence_id == position.object_id,
                        TransportDecisionBinding.step.in_(("PICKING_TASK_TARGET_RACK_IN", TARGET_INBOUND_STEP)),
                        TransportDecisionBinding.window_target_location_code == location,
                        TransportDecisionBinding.window_released_at.is_(None),
                    )
                )
            ).all()
        )
        if len(roots) > 1:
            raise ValueError("target rack has ambiguous current arrival rounds")
        if not roots:
            return None
        task = await self.repository.task_for_binding(db, roots[0])
        fact = (
            (
                await db.scalar(
                    select(InboundEvidence).where(
                        InboundEvidence.source_identity
                        == f"transport:{task.transport_task_id}:outcome:{task.outcome_version}"
                    )
                )
            )
            if task is not None
            else None
        )
        if fact is None:
            return None
        observed = MatchedTargetDeparture(
            line_id,
            pickup.material_execution_id,
            pickup.accept_evidence_id,
            position.object_id,
            task.client_request_id,
            fact.id,
        )
        if not await self.repository.current_arrival(db, line_id, observed):
            return None
        return observed, position

    async def read_in_session(self, db, *, workline_id, material_execution_id, target):
        pickup = await self._material(db, workline_id, material_execution_id, target)
        current = await self._current(db, workline_id, pickup)
        if (
            current is not None
            and current[1].object_id == target.rack_id
            and current[1].arrival_face == target.rack_face
        ):
            return ReadyTarget(workline_id, material_execution_id, target)
        return None


class PickingDepartureReader(PickingTargetReader):
    async def read_in_session(self, db, *, workline_id, material_execution_id, target):
        pickup = await self._material(db, workline_id, material_execution_id, target)
        current = await self._current(db, workline_id, pickup)
        if (
            current is not None
            and current[1].object_id == target.rack_id
            and current[1].arrival_face == target.rack_face
        ):
            return ReadyTarget(workline_id, material_execution_id, target)
        return current[0] if current is not None and current[1].object_id != target.rack_id else None


class PickingPutReader:
    def __init__(self):
        self.materials = MaterialDecideResultReader()

    async def read_in_session(self, db, *, evidence_id):
        fact = await db.get(InboundEvidence, evidence_id, populate_existing=True)
        pickup = (
            (
                await db.scalar(
                    select(AutomaticPickingPickup).where(AutomaticPickingPickup.put_command_code == fact.command_code)
                )
            )
            if fact
            else None
        )
        if pickup is None:
            return None
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.put_command_code))
        finished = await _finished_arm(db, command)
        if finished is None or finished[0].id != evidence_id:
            return None
        work = await PickupRepository.owner(db, pickup)
        execution = await db.get(MaterialExecution, pickup.material_execution_id)
        if (
            work is None
            or execution is None
            or command.material_execution_id != execution.id
            or execution.workline_id != work.workline_id
            or command.workline_id != work.workline_id
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != "put:" + pickup.pickup_id
            or execution.execution_code != pickup.pickup_id
            or execution.material_trace_id != pickup.pickup_id
        ):
            raise ValueError("PUT physical fact lost its original material/Command identity")
        snapshot = (
            await self.materials.by_id(
                db, material_execution_id=execution.id, confirmation_id=pickup.material_confirmation_id
            )
            if pickup.material_confirmation_id is not None
            else await self.materials.by_operation_id(
                db, material_execution_id=execution.id, operation_id=pickup.pickup_id
            )
        )
        if (
            snapshot is None
            or snapshot.published_at is None
            or snapshot.evidence_id != pickup.accept_evidence_id
            or type(snapshot.outcome.result) is not PickingMaterialAccept
        ):
            raise ValueError("PUT facts require this material's published ACCEPT")
        return MatchedPutResult(
            evidence_id,
            work.workline_id,
            pickup.pickup_id,
            execution.id,
            command.command_code,
            command.device_code,
            snapshot.outcome.result.target_locator,
            finished[1].finish_time,
        )
