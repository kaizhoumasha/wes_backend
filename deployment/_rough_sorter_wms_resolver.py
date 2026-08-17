"""粗分机 WMS confirmation request 的持久链重建。"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from deployment._rough_sorter_device_facts import completed_response
from deployment._rough_sorter_transport_recovery_facts import current_rack_id
from deployment._rough_sorter_values import (
    COMMAND_SOURCE_PATTERN,
    base_fact_for_persisted_evidence,
    canonical_evidence_id,
    command_position,
    required_position,
    required_string,
    strict_object,
    wms_position,
)
from src.app.device.repositories.command_repository import device_command_repository
from src.app.execution.models import InboundEvidenceKind
from src.app.execution.repositories import inbound_evidence_repository, material_execution_repository
from src.app.execution.repositories.wms_confirmation_repository import wms_confirmation_repository
from src.app.execution.services.decision_applier import WmsConfirmationRequest
from src.app.resource.repositories import rack_placement_repository
from src.app.runtime.orchestration.repositories.rack_position_repository import workline_rack_position_repository
from src.app.wms_adapter.inbound_wire import parse_outbound_request, parse_outbound_response
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from wes_plugin_sdk import CreateWmsConfirmation, FactReference

    from deployment._rough_sorter_factory import RoughSorterPluginFactFactory
    from deployment._rough_sorter_persistence import (
        DeviceCommandRepositoryPort,
        EvidenceRepositoryPort,
        ExecutionRepositoryPort,
        RackPlacementRepositoryPort,
        RackPositionRepositoryPort,
        WmsConfirmationRepositoryPort,
    )
    from src.app.execution.models import InboundEvidence, MaterialExecution


class RoughSorterWmsConfirmationRequestResolver:
    """从 Decision refs 指向的持久事实重建严格 rough sorter WMS wire。"""

    def __init__(
        self,
        *,
        fact_factory: RoughSorterPluginFactFactory,
        evidence_repository: EvidenceRepositoryPort = inbound_evidence_repository,
        execution_repository: ExecutionRepositoryPort = material_execution_repository,
        wms_confirmation_repository: WmsConfirmationRepositoryPort = wms_confirmation_repository,
        device_command_repository: DeviceCommandRepositoryPort = device_command_repository,
        rack_position_repository: RackPositionRepositoryPort = workline_rack_position_repository,
        rack_placement_repository: RackPlacementRepositoryPort = rack_placement_repository,
    ) -> None:
        self._factory = fact_factory
        self._types = fact_factory.types
        self._evidences = evidence_repository
        self._executions = execution_repository
        self._wms_confirmations = wms_confirmation_repository
        self._commands = device_command_repository
        self._rack_positions = rack_position_repository
        self._rack_placements = rack_placement_repository

    async def resolve(self, db: object, decision: CreateWmsConfirmation) -> WmsConfirmationRequest:
        if not decision.evidence_refs or decision.evidence_refs[0] != decision.fact_id.removeprefix("evidence:"):
            raise ValueError("WMS Decision fact/evidence refs 不匹配")
        evidence_id = decision.evidence_refs[0]
        if not evidence_id.isascii() or not evidence_id.isdigit() or evidence_id.startswith("0"):
            raise ValueError("WMS Decision evidence ref 不是 canonical id")
        evidence = await self._evidences.get_by_id_for_update(db, int(evidence_id))
        execution = await self._executions.get_by_execution_code_for_update(db, decision.material_execution_id)
        if evidence is None or execution is None:
            raise LookupError("WMS Decision refs 指向的持久对象不存在")
        if evidence.material_execution_id != execution.id:
            raise ValueError("WMS Decision evidence/execution correlation 不匹配")
        fact = await self._factory.build(
            db,
            base_fact_for_persisted_evidence(evidence, execution, decision.fact_id),
        )
        request_operation_id = (
            fact.continuation.operation_id
            if type(fact) is self._types.RecoveryDecidedFact
            and type(fact.continuation) is self._types.RecoveryWmsContinuation
            else getattr(fact, "request_operation_id", None)
        )
        if decision.operation_id != request_operation_id:
            raise ValueError("WMS Decision operation_id 与 Fact 不匹配")
        request_payload = (
            await self._recovery_request_payload(db, decision, fact, evidence, execution)
            if type(fact) is self._types.RecoveryDecidedFact
            else self._request_payload(decision, fact, evidence)
        )
        return WmsConfirmationRequest(
            request_payload=parse_outbound_request(request_payload).model_dump(mode="json", exclude_none=True),
            deadline_at=evidence.received_at + timedelta(seconds=30),
        )

    async def _recovery_request_payload(
        self,
        db: object,
        decision: CreateWmsConfirmation,
        fact: Any,
        recovery_evidence: InboundEvidence,
        execution: MaterialExecution,
    ) -> dict[str, Any]:
        continuation = fact.continuation
        if (
            type(continuation) is not self._types.RecoveryWmsContinuation
            or continuation.operation != decision.operation
        ):
            raise ValueError("Recovery WMS Decision 与 typed continuation 不匹配")
        causal = await self._evidences.get_by_id_for_update(
            db,
            canonical_evidence_id(fact.reconciling_evidence_id, "causal"),
        )
        if causal is None:
            raise LookupError("Recovery causal evidence 不存在")
        timestamp_ms = int(timezone.to_utc(recovery_evidence.received_at).timestamp() * 1000)
        if causal.kind == InboundEvidenceKind.WMS_RESULT:
            confirmation = await self._wms_confirmations.get_by_identity_for_update(
                db,
                required_string(causal.operation, "causal.operation"),
                required_string(causal.operation_id, "causal.operation_id"),
            )
            if confirmation is None or confirmation.material_execution_id != execution.id:
                raise ValueError("Recovery causal WMS confirmation correlation 不匹配")
            payload = cast("dict[str, Any]", json.loads(json.dumps(confirmation.request_payload)))
            payload["operation_id"] = decision.operation_id
            payload["timestamp"] = timestamp_ms
            return payload
        if causal.kind != InboundEvidenceKind.DEVICE_RESULT:
            raise ValueError("Recovery WMS continuation causal kind 不可执行")
        command = await self._commands.get_by_command_code(
            db,
            required_string(causal.command_code, "causal.command_code"),
            for_update=True,
        )
        if command is None or command.material_execution_id != execution.id:
            raise ValueError("Recovery causal command correlation 不匹配")
        params = strict_object(command.params, {"material_trace_id", "source", "target"}, "DeviceCommand params")
        target = command_position(params["target"], execution.material_trace_id)
        common: dict[str, Any] = {
            "operation_id": decision.operation_id,
            "operation": decision.operation,
            "timestamp": timestamp_ms,
        }
        if decision.operation == "inbound.material.target_decide@v1":
            admission = await completed_response(
                db=db,
                execution=execution,
                operation="inbound.material.admission_decide@v1",
                confirmations=self._wms_confirmations,
                evidences=self._evidences,
            )
            common["data"] = {
                "material_execution_id": execution.execution_code,
                "material_trace_id": execution.material_trace_id,
                "pkg_id": admission.get("pkg_id"),
                "inbound_admission_id": admission.get("inbound_admission_id"),
                "source_position": wms_position(target),
                "current_rack_id": await current_rack_id(
                    db=db,
                    runtime=fact.runtime_snapshot,
                    rack_positions=self._rack_positions,
                    rack_placements=self._rack_placements,
                ),
            }
            return common
        if decision.operation == "inbound.material.placement_report@v1":
            admission = await completed_response(
                db=db,
                execution=execution,
                operation="inbound.material.admission_decide@v1",
                confirmations=self._wms_confirmations,
                evidences=self._evidences,
            )
            assigned = await completed_response(
                db=db,
                execution=execution,
                operation="inbound.material.target_decide@v1",
                confirmations=self._wms_confirmations,
                evidences=self._evidences,
            )
            common["data"] = {
                "material_execution_id": execution.execution_code,
                "material_trace_id": execution.material_trace_id,
                "pkg_id": admission.get("pkg_id"),
                "inbound_admission_id": admission.get("inbound_admission_id"),
                "target_assignment_id": assigned.get("target_assignment_id"),
                "target_position": wms_position(target),
                "placement_sequence": assigned.get("placement_sequence"),
                "command_code": command.command_code,
                "placed_at": timestamp_ms,
            }
            return common
        source_match = COMMAND_SOURCE_PATTERN.fullmatch(command.execution_ref_id)
        source = (
            await self._evidences.get_by_id_for_update(db, int(source_match.group(1)))
            if source_match is not None
            else None
        )
        if source is None:
            raise ValueError("Recovery NG command source evidence missing")
        source_response = parse_outbound_response(
            required_string(source.operation, "source.operation"),
            200,
            source.normalized_payload,
        ).model_dump(mode="json", exclude_none=True)
        source_data = cast("dict[str, Any]", source_response["data"])
        common["data"] = {
            "material_execution_id": execution.execution_code,
            "material_trace_id": execution.material_trace_id,
            "ng_evidence_id": str(causal.id),
            "ng_position": wms_position(target),
            "reason_code": source_data.get("reason_code"),
            "business_context": "ROUGH_SORT_INBOUND",
        }
        return common

    def _request_payload(
        self,
        decision: CreateWmsConfirmation,
        fact: FactReference,
        evidence: InboundEvidence,
    ) -> dict[str, Any]:
        common: dict[str, Any] = {
            "operation_id": decision.operation_id,
            "operation": decision.operation,
            "timestamp": int(timezone.to_utc(evidence.received_at).timestamp() * 1000),
        }
        types = self._types
        if (
            type(fact) is types.MaterialEvidenceReadyFact
            and decision.operation == "inbound.material.admission_decide@v1"
        ):
            common["data"] = {
                "material_execution_id": fact.material_execution_id,
                "material_trace_id": fact.material_trace_id,
                "six_in_one": {
                    "LotCode": fact.lot_code,
                    "DateCode": fact.date_code,
                    "Qty": fact.qty,
                    "ProductNo": fact.product_no,
                    "MfrPN": fact.mfr_pn,
                    "PONumber": fact.po_number,
                },
                "measurements": {"diameter_mm": fact.diameter_mm, "thickness_mm": fact.thickness_mm},
                "shape_result": fact.shape_result.value,
                "line_run_epoch_id": fact.line_run_epoch_id,
                "workline_code": fact.workline_code,
                "source_position": wms_position(fact.source_position),
            }
            return common
        if (
            type(fact) is types.TransportOutcomePublishedFact
            and decision.operation == "inbound.material.target_decide@v1"
        ):
            if fact.outcome is not types.TransportOutcome.SUCCEEDED:
                raise ValueError("target_decide 只接受成功 NEW_IN Transport Fact")
            common["data"] = {
                "material_execution_id": fact.material_execution_id,
                "material_trace_id": fact.material_trace_id,
                "pkg_id": fact.pkg_id,
                "inbound_admission_id": fact.inbound_admission_id,
                "source_position": wms_position(required_position(fact.source_position, "source_position")),
                "current_rack_id": fact.rack_id,
            }
            return common
        if type(fact) is types.DevicePositionConfirmedFact:
            if (
                decision.operation == "inbound.material.target_decide@v1"
                and fact.step is types.DeviceStep.TRANSFER_TO_OUTLET
            ):
                common["data"] = {
                    "material_execution_id": fact.material_execution_id,
                    "material_trace_id": fact.material_trace_id,
                    "pkg_id": fact.pkg_id,
                    "inbound_admission_id": fact.inbound_admission_id,
                    "source_position": wms_position(required_position(fact.actual_position, "actual_position")),
                    "current_rack_id": fact.current_rack_id,
                }
                return common
            if (
                decision.operation == "inbound.material.placement_report@v1"
                and fact.step is types.DeviceStep.PLACEMENT_TO_CELL
            ):
                common["data"] = {
                    "material_execution_id": fact.material_execution_id,
                    "material_trace_id": fact.material_trace_id,
                    "pkg_id": fact.pkg_id,
                    "inbound_admission_id": fact.inbound_admission_id,
                    "target_assignment_id": fact.target_assignment_id,
                    "target_position": wms_position(fact.target_position),
                    "placement_sequence": fact.placement_sequence,
                    "command_code": fact.command_code,
                    "placed_at": fact.placed_at_ms,
                }
                return common
            if decision.operation == "inbound.material.ng_placement_report@v1" and fact.step in {
                types.DeviceStep.MEASUREMENT_TO_NG,
                types.DeviceStep.PLACEMENT_TO_NG,
            }:
                common["data"] = {
                    "material_execution_id": fact.material_execution_id,
                    "material_trace_id": fact.material_trace_id,
                    "ng_evidence_id": fact.ng_evidence_id,
                    "ng_position": wms_position(fact.target_position),
                    "reason_code": fact.reason_code,
                    "business_context": "ROUGH_SORT_INBOUND",
                }
                return common
        if (
            type(fact) is types.TargetDecidedFact
            and decision.operation == "inbound.source_rack.replacement_plan_decide@v1"
        ):
            common["data"] = {
                "material_execution_id": fact.material_execution_id,
                "material_trace_id": fact.material_trace_id,
                "current_rack_id": fact.current_rack_id,
            }
            return common
        raise ValueError("WMS operation 与持久 Fact 类型不匹配")


__all__ = ["RoughSorterWmsConfirmationRequestResolver"]
