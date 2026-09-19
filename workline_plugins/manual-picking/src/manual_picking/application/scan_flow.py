"""四点扫码 Evidence 在原事务内落成本次经过及可靠义务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from wes_plugin_sdk import ReturnBufferDrainReady, ReturnBufferDrainWait, wms_operations

from manual_picking.definition import DEFINITION, FIVE_RACK, INLET, OUTLET, TRANSFER_RACK
from manual_picking.handlers import Scan1Handler, Scan2Handler, Scan3Handler, Scan4Handler
from manual_picking.handlers.scan_types import PassageSnapshot, ScanFact, normal_bin_code, scanned_bin_identity
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest, EcsDeviceEvent
from src.app.device.models.command import CommandStatus
from src.app.device.repositories.command_repository import device_command_repository
from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.plugin_binding import BusinessEvidenceApplication, BusinessEvidenceDisposition
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.app.execution.repositories.position_projection_repository import position_projection_repository
from src.app.transport.repository import TransportRepository
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.workline.installed_plugin import parse_device_bindings
from src.app.workline.repositories.workline_repository import workline_repository
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

from .batch_driver import SOURCE_RACK_OUT_STEP, SOURCE_RACK_ROTATE_STEP, TRANSFER_RACK_OUT_STEP
from .drain_repository import DRAIN_RACK_IN_STEP, DRAIN_RACK_OUT_STEP
from .passage_model import ManualPickingPassage
from .passage_repository import PassageRepository

_WAIT_FOR_RESULT = "WAIT_FOR_RESULT"


def _projection_at(projection: Any, workline_id: int, kind: str, location: str, face: str | None = None) -> bool:
    return bool(
        projection is not None
        and projection.workline_id == workline_id
        and not projection.position_unknown
        and projection.position_json == {"kind": kind, "location_code": location}
        and (face is None or projection.arrival_face == face)
    )


class ManualPickingScanFlow:
    def __init__(
        self,
        *,
        commands: Any,
        admissions: Any,
        evidences: Any = inbound_evidence_repository,
        worklines: Any = workline_repository,
        tasks: Any = picking_task_repository,
        passages: Any = None,
        command_reader: Any = device_command_repository,
        wms_reader: Any = None,
        transport_reader: Any = None,
        source_racks: Any = None,
        position_reader: Any = position_projection_repository,
        batch_reader: Any = None,
        batch_result: Any = None,
        drain_repository: Any = None,
        drain_reader: Any = None,
    ) -> None:
        self._drains = drain_repository
        self._drain_reader = drain_reader
        self._commands = commands
        self._admissions = admissions
        self._evidences = evidences
        self._worklines = worklines
        self._tasks = tasks
        self._passages = passages or PassageRepository()
        self._command_reader = command_reader
        self._wms_reader = wms_reader
        self._transport_reader = transport_reader or TransportRepository()
        self._source_racks = source_racks or PickingTaskPlanDeltaRepository()
        self._positions = position_reader
        self._batch_reader = batch_reader
        self._batch_result = batch_result
        self._scan1 = Scan1Handler()
        self._scan2 = Scan2Handler()
        self._scan3 = Scan3Handler()
        self._scan4 = Scan4Handler()

    async def apply_in_session(self, db: Any, evidence_id: int, *, workline_id: int) -> BusinessEvidenceApplication:
        evidence = await self._evidences.get_by_id_for_update(db, evidence_id)
        workline = await self._worklines.get_for_update(db, workline_id)
        if (
            evidence is None
            or evidence.workline_id != workline_id
            or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
            or workline is None
            or not workline.is_active
            or (workline.plugin_key, workline.plugin_version) != (DEFINITION.plugin_key, DEFINITION.plugin_version)
        ):
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        bindings = parse_device_bindings(workline.config, DEFINITION.device_roles)
        if evidence.kind == InboundEvidenceKind.WMS_RESULT:
            if evidence.operation in {BIN_INBOUND_BATCH_OPERATION, BIN_RETURN_BATCH_OPERATION}:
                result = await self._apply_batch_result(db, evidence, workline)
                role = "WMS_BATCH"
            elif evidence.operation == RETURN_BUFFER_DRAIN_OPERATION:
                if self._drain_reader is None:
                    return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
                intent, outcome = await self._drain_reader.read(db, evidence, workline_id=workline_id)
                result = (
                    "DRAIN_RECEIVED"
                    if (
                        intent.workline_code == workline.line_code
                        and isinstance(outcome.result, (ReturnBufferDrainReady, ReturnBufferDrainWait))
                    )
                    else None
                )
                role = "WMS_DRAIN"
            elif evidence.operation == RACK_DEPARTURE_OPERATION:
                result = "DEPARTURE_RECEIVED"
                role = "WMS_DEPARTURE"
            else:
                result = await self._apply_admission_result(db, evidence, workline_id, bindings)
                role = "WMS_ADMISSION"
        elif evidence.kind == InboundEvidenceKind.WMS_EVENT:
            result = await self._apply_completed(db, evidence, workline_id, bindings)
            role = "WMS_COMPLETED"
        elif evidence.kind == InboundEvidenceKind.DEVICE_EVENT:
            result, role = await self._apply_device_event(db, evidence, workline, bindings)
        elif evidence.kind == InboundEvidenceKind.DEVICE_RESULT:
            outlet_location = workline.position_bindings[OUTLET.slot_key]["location_id"]
            result = await self._apply_device_result(db, evidence, workline_id, bindings, outlet_location)
            role = "DEVICE_RESULT"
        elif evidence.kind == InboundEvidenceKind.TRANSPORT_RESULT:
            result = await self._apply_transport_result(db, evidence, workline_id)
            role = "TRANSPORT_RESULT"
        else:
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        if result == "IGNORED":
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.IGNORED)
        if result == _WAIT_FOR_RESULT:
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.DEFERRED, retry_after_ms=1000)
        if result is None:
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        return BusinessEvidenceApplication(
            BusinessEvidenceDisposition.APPLIED,
            canonical_json_digest({"evidence_id": evidence_id, "role": role, "result": result}),
        )

    async def _apply_batch_result(self, db: Any, evidence: Any, workline: Any) -> str | None:  # noqa: PLR0911
        if self._batch_reader is None or self._batch_result is None:
            return None
        positions = {role: binding["location_id"] for role, binding in workline.position_bindings.items()}
        if evidence.operation == BIN_RETURN_BATCH_OPERATION:
            intent, _ = await self._batch_reader.read_return(db, evidence, workline_id=workline.id)
            if intent.workline_code != workline.line_code:
                return None
            source = await self._positions.get(db, "RACK", intent.rack_id)
            drain = await self._drains.current(db, workline.id) if self._drains is not None else None
            if drain is not None:
                if not isinstance(drain.result, ReturnBufferDrainReady) or (
                    intent.rack_id,
                    intent.rack_face,
                ) not in {(rack.rack_id, face) for rack in drain.result.racks for face in rack.rack_faces}:
                    return None
                ingress = await self._drains.transport(db, drain, DRAIN_RACK_IN_STEP, intent.rack_id)
                if (
                    source is None
                    or ingress is None
                    or not await self._drains.arrival_matches(db, ingress, source, intent.rack_id, intent.rack_face)
                ):
                    return None
            elif not await self._source_racks.has_applied_source_face(
                db, workline.id, intent.rack_id, intent.rack_face
            ):
                return None
            readiness = await self._position_readiness(
                db, source, workline.id, "RACK_POSITION", positions[FIVE_RACK.slot_key], intent.rack_face
            )
            if readiness != "READY":
                return readiness
            return await self._batch_result.apply_return_in_session(
                db,
                evidence,
                workline_id=workline.id,
                workline_code=workline.line_code,
                confirmed_rack_id=intent.rack_id,
                confirmed_face=intent.rack_face,
                return_location=positions["OUTLET"],
            )
        task = await self._tasks.get_executing_for_workline_for_update(db, workline.id)
        if task is None:
            return None
        intent, _ = await self._batch_reader.read_inbound(db, evidence, workline_id=workline.id)
        if intent.task_id != task.task_id or not any(
            source.rack_id == intent.rack_id and source.rack_face == intent.rack_face
            for source in await self._source_racks.list_bin_source_racks(db, task.id)
        ):
            return None
        source = await self._positions.get(db, "RACK", intent.rack_id)
        readiness = await self._position_readiness(
            db, source, workline.id, "RACK_POSITION", positions[FIVE_RACK.slot_key], intent.rack_face
        )
        if readiness != "READY":
            return readiness
        return await self._batch_result.apply_inbound_in_session(
            db,
            evidence,
            workline_id=workline.id,
            confirmed_rack_id=intent.rack_id,
            confirmed_face=intent.rack_face,
            inlet_location=positions[INLET.slot_key],
        )

    async def _apply_transport_result(self, db: Any, evidence: Any, workline_id: int) -> str | None:
        payload = evidence.normalized_payload
        if payload.get("step") in {"MANUAL_PICKING_INBOUND_BATCH", "MANUAL_PICKING_RETURN_BATCH"}:
            return await self._apply_batch_transport_result(db, evidence, workline_id)
        if (
            evidence.transport_task_id != payload.get("transport_task_id")
            or payload.get("caller", {}).get("workline_id") != str(workline_id)
            or payload.get("step")
            not in {
                "PICKING_TASK_TARGET_RACK_IN",
                "PICKING_TASK_BIN_SOURCE_RACK_IN",
                SOURCE_RACK_ROTATE_STEP,
                SOURCE_RACK_OUT_STEP,
                TRANSFER_RACK_OUT_STEP,
                DRAIN_RACK_IN_STEP,
                DRAIN_RACK_OUT_STEP,
            }
        ):
            return None
        task = await self._transport_reader.get_task(db, evidence.transport_task_id, for_update=True)
        if (
            task is None
            or task.client_request_id != payload.get("client_request_id")
            or task.request_json.get("rack_id") != payload.get("rack_id")
        ):
            return None
        status = payload.get("status")
        if status == "SUCCEEDED":
            members = payload.get("members")
            expected_position = (
                task.request_json.get("position")
                if payload.get("step") == SOURCE_RACK_ROTATE_STEP
                else task.request_json.get("target")
            )
            actual_position = (
                members[0].get("final_position") if isinstance(members, list) and len(members) == 1 else None
            )
            matching_position = (
                isinstance(actual_position, dict)
                and actual_position.get("kind") == "RACK_POSITION"
                and isinstance(actual_position.get("location_code"), str)
                and bool(actual_position["location_code"])
                if isinstance(expected_position, dict) and expected_position.get("kind") == "ZONE"
                else actual_position == expected_position
            )
            if (
                not isinstance(members, list)
                or len(members) != 1
                or members[0].get("object_id") != payload["rack_id"]
                or not matching_position
                or (
                    payload.get("step") not in {SOURCE_RACK_OUT_STEP, TRANSFER_RACK_OUT_STEP, DRAIN_RACK_OUT_STEP}
                    and members[0].get("arrival_face") != task.request_json.get("target_face")
                )
            ):
                return None
        elif status not in {"FAILED", "REJECTED", "UNKNOWN"}:
            return None
        return status

    async def _apply_batch_transport_result(self, db: Any, evidence: Any, workline_id: int) -> str | None:
        payload = evidence.normalized_payload
        if (
            evidence.transport_task_id != payload.get("transport_task_id")
            or payload.get("caller", {}).get("workline_id") != str(workline_id)
            or not isinstance(payload.get("batch_operation_id"), str)
        ):
            return None
        task = await self._transport_reader.get_task(db, evidence.transport_task_id, for_update=True)
        if task is None or task.kind != "BIN_MOVE" or task.client_request_id != payload.get("client_request_id"):
            return None
        status = payload.get("status")
        if status in {"FAILED", "REJECTED", "UNKNOWN"}:
            return status
        if status != "SUCCEEDED":
            return None
        moves = task.request_json.get("moves")
        members = payload.get("members")
        if (
            not isinstance(moves, list)
            or not isinstance(members, list)
            or len(moves) != len(members)
            or any(
                member.get("object_id") != move.get("bin_code") or member.get("final_position") != move.get("target")
                for move, member in zip(moves, members, strict=True)
            )
        ):
            return None
        if payload["step"] == "MANUAL_PICKING_RETURN_BATCH":
            rows = await self._passages.unfinished_return_prefix_for_update(db, workline_id)
            if len(rows) < len(moves) or any(
                row.return_state != "RETURN_REQUESTED" or row.bin_code != move["bin_code"]
                for row, move in zip(rows, moves, strict=False)
            ):
                return None
            for row in rows[: len(moves)]:
                row.return_state = "RETURNED"
                row.disposition = "CLOSED"
        return status

    async def _apply_device_event(
        self, db: Any, evidence: Any, workline: Any, bindings: dict[str, str]
    ) -> tuple[str | None, str]:
        workline_id = cast("int", workline.id)
        event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
        if event.event_type != "SCAN_COMPLETED" or event.is_debug:
            return "IGNORED", "DEVICE_OTHER"
        role = next((key for key, device_code in bindings.items() if device_code == evidence.device_code), None)
        if role is None:
            return None, "DEVICE_UNBOUND"
        # ECS 扫码合同固定使用 bin_code。
        raw = event.data.get("bin_code")
        raw_code = raw if isinstance(raw, str) else None
        if role == "SCAN1":
            positions = {role: binding["location_id"] for role, binding in workline.position_bindings.items()}
            result = await self._apply_scan1(db, evidence, workline_id, bindings, positions, raw_code)
        elif role == "SCAN2":
            result = await self._apply_scan2(db, evidence, workline_id, bindings, raw_code, event.timestamp)
        elif role == "SCAN3":
            result = await self._apply_scan3(db, evidence, workline_id, bindings, raw_code)
        elif role == "SCAN4":
            result = await self._apply_scan4(db, evidence, workline_id, bindings, raw_code)
        else:
            return None, role
        return result, role

    async def _apply_admission_result(
        self, db: Any, evidence: Any, workline_id: int, bindings: dict[str, str]
    ) -> str | None:
        outcome = self._wms_reader.decode_admission_outcome(evidence.normalized_payload)
        if evidence.operation_id != outcome.operation_id:
            return None
        passage = await self._passages.by_admission_operation_for_update(db, outcome.operation_id)
        if passage is None or passage.workline_id != workline_id or passage.scan2_evidence_id is None:
            return None
        if outcome.result == "WORK_REQUIRED" and outcome.task_id != passage.task_id:
            return None
        if outcome.result == "WAIT" and (passage.admission_scanned_at is None or outcome.retry_after_ms is None):
            return None
        if outcome.result == "NO_WORK" and passage.wms_result is not None:
            return None
        if (outcome.result == "NO_WORK" or (outcome.result == "WORK_REQUIRED" and passage.wms_result is not None)) and (
            await self._device_has_unclosed(db, workline_id, bindings, "SCAN2")
        ):
            return None
        passage.admission_result = outcome.result
        if outcome.result == "NO_WORK":
            passage.disposition = "NORMAL"
            passage.wms_result = "NORMAL"
            passage.scan2_command_code = await self._move(
                db, workline_id, bindings, "SCAN2", passage.scan2_evidence_id, "MOVE_FORWARD"
            )
        elif outcome.result == "WAIT":
            operation_id = new_uuid7()
            passage.admission_operation_id = operation_id
            now = timezone.now_for_db()
            intent = wms_operations.outbound_manual_bin_work_admission(
                operation_id=operation_id,
                task_id=passage.task_id,
                bin_code=cast("str", passage.bin_code),
                scanned_at=cast("int", passage.admission_scanned_at),
            )
            await self._admissions.create_in_session(
                db,
                intent,
                workline_id=workline_id,
                created_at=now,
                not_before=now + timedelta(milliseconds=outcome.retry_after_ms),
            )
        return outcome.result

    async def _apply_scan3(
        self, db: Any, evidence: Any, workline_id: int, bindings: dict[str, str], raw_code: str | None
    ) -> str | None:
        code = normal_bin_code(raw_code, "-B")
        passage = (
            await self._passages.unique_open_bin_for_update(
                db, workline_id=workline_id, bin_code=code, after_scan2=None
            )
            if code is not None
            else None
        )
        if passage is not None and passage.scan3_evidence_id is not None:
            return None
        if await self._device_has_unclosed(db, workline_id, bindings, "SCAN3"):
            return _WAIT_FOR_RESULT if passage is not None else None
        normal_authorized = False
        if passage is not None and passage.disposition == "NORMAL":
            preceding_code = passage.scan2_command_code
            command = await self._command_reader.get_by_command_code(db, preceding_code) if preceding_code else None
            normal_authorized = command is not None and command.status == CommandStatus.SUCCEEDED
        decision = self._scan3.decide(
            ScanFact(
                "SCAN3",
                evidence.id,
                raw_code,
                PassageSnapshot(
                    bin_code=passage.bin_code,
                    ng=passage.disposition == "NG",
                    normal_authorized=normal_authorized,
                )
                if passage
                else None,
                passage.task_id if passage else None,
            )
        )
        command_code = await self._move(db, workline_id, bindings, "SCAN3", evidence.id, decision.route)
        if passage is not None:
            passage.scan3_evidence_id = evidence.id
            passage.scan3_command_code = command_code
            passage.scan3_route = decision.route
            if decision.route == "MOVE_LEFT":
                passage.disposition = "NG"
        return decision.route

    async def _apply_scan4(
        self, db: Any, evidence: Any, workline_id: int, bindings: dict[str, str], raw_code: str | None
    ) -> str | None:
        if await self._device_has_unclosed(db, workline_id, bindings, "SCAN4"):
            return None
        code = normal_bin_code(raw_code, "-B")
        passage = (
            await self._passages.unique_open_bin_for_update(
                db, workline_id=workline_id, bin_code=code, after_scan2=True
            )
            if code is not None
            else None
        )
        if passage is not None and passage.scan4_evidence_id is not None:
            return None
        scan3_forward = False
        if passage is not None and passage.scan3_route == "MOVE_FORWARD":
            preceding_code = passage.scan3_command_code
            command = await self._command_reader.get_by_command_code(db, preceding_code) if preceding_code else None
            scan3_forward = command is not None and command.status == CommandStatus.SUCCEEDED
        decision = self._scan4.decide(
            ScanFact(
                "SCAN4",
                evidence.id,
                raw_code,
                PassageSnapshot(
                    bin_code=passage.bin_code,
                    ng=passage.disposition == "NG",
                    normal_authorized=passage.disposition == "NORMAL",
                    scan3_forward=scan3_forward,
                )
                if passage
                else None,
                passage.task_id if passage else None,
            )
        )
        if decision.route == "HOLD" or passage is None:
            return None
        passage.scan4_evidence_id = evidence.id
        passage.scan4_received_at = evidence.received_at
        passage.return_state = "MOVE_PENDING"
        passage.scan4_command_code = await self._move(db, workline_id, bindings, "SCAN4", evidence.id, decision.route)
        return decision.route

    async def _apply_device_result(  # noqa: PLR0911
        self, db: Any, evidence: Any, workline_id: int, bindings: dict[str, str], outlet_location: str
    ) -> str | None:
        command_code = evidence.command_code
        if not command_code:
            return None
        passage = await self._passages.by_command_code_for_update(db, command_code)
        if passage is not None and passage.workline_id != workline_id:
            return None
        command = await self._command_reader.get_by_command_code(db, command_code)
        if command is None:
            return None
        if passage is None:
            identity = command.execution_ref_id.split(":")
            if (
                command.workline_id == workline_id
                and command.device_code == evidence.device_code == bindings["SCAN3"]
                and command.execution_ref_type == WORKLINE_BUSINESS_REF_TYPE
                and len(identity) == 3
                and identity[0] == "manual-picking"
                and identity[1].isdigit()
                and identity[2] == "SCAN3"
                and command.task_type == "MOVE_LEFT"
                and command.status == CommandStatus.SUCCEEDED
            ):
                return "UNKNOWN_BIN_NG_EXIT_CLOSED"
            return None
        if passage.scan4_command_code == command_code:
            if passage.return_state != "MOVE_PENDING" or command.status != CommandStatus.SUCCEEDED:
                return None
            projection = await self._positions.get(db, "BIN", passage.bin_code, for_update=True)
            if projection is None or projection.workline_id != workline_id:
                return None
            projection.position_json = {"kind": "HANDOFF_POSITION", "location_code": outlet_location}
            projection.position_unknown = False
            projection.arrival_face = None
            await self._positions.flush(db)
            passage.return_state = "READY"
            return "RETURN_BUFFER_READY"
        if passage.scan3_command_code == command_code and passage.scan3_route == "MOVE_LEFT":
            if command.status != CommandStatus.SUCCEEDED:
                return None
            passage.disposition = "CLOSED"
            return "NG_EXIT_CLOSED"
        return "COMMAND_RESULT_RECORDED"

    async def _device_has_unclosed(self, db: Any, workline_id: int, bindings: dict[str, str], role: str) -> bool:
        return await self._command_reader.has_unclosed_for_device_for_update(
            db, workline_id=workline_id, device_code=bindings[role]
        )

    async def _apply_completed(self, db: Any, evidence: Any, workline_id: int, bindings: dict[str, str]) -> str | None:
        completed = self._wms_reader.decode_completed_fact(evidence.normalized_payload)
        passage = await self._passages.waiting_for_completion_for_update(
            db, workline_id=workline_id, task_id=completed.task_id, bin_code=completed.bin_code
        )
        if passage is None:
            completed_passage = await self._passages.uniquely_completed_for_update(
                db, workline_id=workline_id, task_id=completed.task_id, bin_code=completed.bin_code
            )
            return (
                "DUPLICATE_COMPLETION"
                if completed_passage and completed_passage.wms_result == completed.result
                else None
            )
        if (
            passage.scan2_evidence_id is None
            or passage.admission_scanned_at is None
            or completed.completed_at < passage.admission_scanned_at
            or passage.admission_result != "WORK_REQUIRED"
        ):
            return None
        if await self._device_has_unclosed(db, workline_id, bindings, "SCAN2"):
            return None
        passage.wms_result = completed.result
        passage.wms_completed_at = datetime.fromtimestamp(completed.completed_at / 1000, UTC).replace(tzinfo=None)
        passage.wms_completed_evidence_id = evidence.id
        passage.disposition = completed.result
        if completed.result == "NG":
            passage.reason_code = "MANUAL_PICK_NG"
        passage.scan2_command_code = await self._move(
            db, workline_id, bindings, "SCAN2", passage.scan2_evidence_id, "MOVE_FORWARD"
        )
        return completed.result

    async def _apply_scan1(
        self,
        db: Any,
        evidence: Any,
        workline_id: int,
        bindings: dict[str, str],
        positions: dict[str, str],
        raw_code: str | None,
    ) -> str | None:
        task = await self._tasks.get_executing_for_workline_for_update(db, workline_id)
        if task is None:
            return None
        identity = scanned_bin_identity(raw_code)
        prior_passages = await self._passages.scan1_unclosed_for_update(db, workline_id)
        if identity is not None and any(prior.bin_code == identity for prior in prior_passages):
            return None
        if await self._device_has_unclosed(db, workline_id, bindings, "SCAN1"):
            return _WAIT_FOR_RESULT
        for prior in prior_passages:
            if prior.scan1_command_code is None:
                return None
            prior_command = await self._command_reader.get_by_command_code(db, prior.scan1_command_code)
            if prior_command is None or prior_command.status != CommandStatus.SUCCEEDED:
                return (
                    _WAIT_FOR_RESULT
                    if prior_command is not None and prior_command.status != CommandStatus.FAILED
                    else None
                )
        decision = self._scan1.decide(ScanFact("SCAN1", cast("int", evidence.id), raw_code, None, task.task_id))
        if decision.route == "MOVE_FORWARD":
            readiness = await self._scan1_physical_readiness(
                db, task, workline_id, positions, cast("str", decision.normal_bin_code)
            )
            if readiness != "READY":
                return readiness
        passage = await self._passages.add(
            db,
            ManualPickingPassage(
                workline_id=workline_id,
                task_id=task.task_id,
                bin_code=decision.normal_bin_code,
                raw_scan1_code=raw_code,
                scan1_evidence_id=evidence.id,
                scan1_received_at=evidence.received_at,
                disposition="NG" if decision.ng_reason else "OPEN",
            ),
        )
        passage.scan1_command_code = await self._move(db, workline_id, bindings, "SCAN1", evidence.id, decision.route)
        return decision.route

    async def _scan1_physical_readiness(
        self, db: Any, task: Any, workline_id: int, positions: dict[str, str], bin_code: str
    ) -> str | None:
        if not task.target_rack_id or not task.target_rack_face:
            return _WAIT_FOR_RESULT
        target = await self._positions.get(db, "RACK", task.target_rack_id)
        target_state = await self._position_readiness(
            db, target, workline_id, "RACK_POSITION", positions[TRANSFER_RACK.slot_key], task.target_rack_face
        )
        if target_state != "READY":
            return target_state
        bin_position = await self._positions.get(db, "BIN", bin_code)
        bin_state = await self._position_readiness(
            db, bin_position, workline_id, "HANDOFF_POSITION", positions[INLET.slot_key]
        )
        if bin_state != "READY":
            return bin_state
        transport = await self._transport_reader.get_task(db, bin_position.source_transport_task_id)
        if transport is None or transport.kind != "BIN_MOVE":
            return None
        moves = transport.request_json.get("moves")
        if not isinstance(moves, list):
            return None
        sources = [move.get("source") for move in moves if isinstance(move, dict) and move.get("bin_code") == bin_code]
        if len(sources) != 1 or not isinstance(sources[0], dict):
            return None
        source = sources[0]
        if not any(
            planned.rack_id == source.get("rack_id") and planned.rack_face == source.get("rack_face")
            for planned in await self._source_racks.list_bin_source_racks(db, task.id)
        ):
            return None
        return "READY"

    async def _position_readiness(
        self,
        db: Any,
        projection: Any,
        workline_id: int,
        kind: str,
        location: str,
        face: str | None = None,
    ) -> str | None:
        if projection is None:
            return _WAIT_FOR_RESULT
        if projection.position_unknown:
            return None
        if not _projection_at(projection, workline_id, kind, location, face):
            return _WAIT_FOR_RESULT
        transport = await self._transport_reader.get_task(db, projection.source_transport_task_id)
        if transport is None or transport.transport_task_id != projection.source_transport_task_id:
            return None
        if transport.status == "SUCCEEDED":
            return "READY"
        return _WAIT_FOR_RESULT if transport.status in {"PENDING", "ACCEPTED"} else None

    async def _apply_scan2(
        self,
        db: Any,
        evidence: Any,
        workline_id: int,
        bindings: dict[str, str],
        raw_code: str | None,
        scanned_at: int,
    ) -> str | None:
        in_flight = await self._passages.scan2_in_flight_for_update(db, workline_id)
        identity = scanned_bin_identity(raw_code)
        duplicate = identity is not None and any(prior.bin_code == identity for prior in in_flight)
        if duplicate or await self._device_has_unclosed(db, workline_id, bindings, "SCAN2"):
            return None if duplicate else _WAIT_FOR_RESULT
        for prior in in_flight:
            if prior.scan2_command_code is None:
                return _WAIT_FOR_RESULT
            prior_command = await self._command_reader.get_by_command_code(db, prior.scan2_command_code)
            if prior_command is None or prior_command.status != CommandStatus.SUCCEEDED:
                return (
                    _WAIT_FOR_RESULT
                    if prior_command is not None and prior_command.status != CommandStatus.FAILED
                    else None
                )
        passage = await self._passages.scan2_head_for_update(db, workline_id)
        if passage is None or passage.scan2_fault_evidence_id is not None:
            return None
        if passage.scan1_command_code is None:
            return None
        command = await self._command_reader.get_by_command_code(db, passage.scan1_command_code)
        if command is None or command.status != CommandStatus.SUCCEEDED:
            return _WAIT_FOR_RESULT if command is not None and command.status != CommandStatus.FAILED else None
        if identity is None or identity != passage.bin_code:
            command_code = await self._move(db, workline_id, bindings, "SCAN2", evidence.id, "MOVE_FORWARD")
            passage.scan2_fault_evidence_id = evidence.id
            passage.scan2_fault_command_code = command_code
            return "NG_MOVE_FORWARD"
        decision = self._scan2.decide(
            ScanFact(
                "SCAN2",
                evidence.id,
                raw_code,
                PassageSnapshot(bin_code=passage.bin_code, ng=passage.disposition == "NG"),
                passage.task_id,
            )
        )
        passage.scan2_evidence_id = evidence.id
        if decision.route == "REQUEST_WMS":
            operation_id = new_uuid7()
            passage.admission_operation_id = operation_id
            passage.admission_scanned_at = scanned_at
            intent = wms_operations.outbound_manual_bin_work_admission(
                operation_id=operation_id,
                task_id=passage.task_id,
                bin_code=cast("str", decision.normal_bin_code),
                scanned_at=scanned_at,
            )
            await self._admissions.create_in_session(
                db, intent, workline_id=workline_id, created_at=timezone.now_for_db()
            )
        else:
            passage.disposition = "NG"
            passage.scan2_command_code = await self._move(
                db, workline_id, bindings, "SCAN2", evidence.id, decision.route
            )
        return decision.route

    async def _move(
        self, db: Any, workline_id: int, bindings: dict[str, str], role: str, evidence_id: int, route: str
    ) -> str:
        device_code = bindings[role]
        binding = await self._worklines.get_binding_for_command_creation(
            db, workline_id=workline_id, device_code=device_code
        )
        if binding is None:
            raise ValueError("扫码设备缺少冻结命令合同")
        handle = await self._commands.create_command_in_session(
            db,
            DeviceCommandRequest(
                device_code=device_code,
                workline_id=workline_id,
                execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
                execution_ref_id=f"manual-picking:{evidence_id}:{role}",
                material_execution_id=None,
                contract_key=binding.contract_key,
                contract_version=binding.contract_version,
                task_type=route,
                params={},
                deadline_at=timezone.now_for_db() + timedelta(milliseconds=binding.command_timeout_ms),
            ),
        )
        return cast("str", handle.command_code)


__all__ = ["ManualPickingScanFlow"]
