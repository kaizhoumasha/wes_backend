"""粗分机 Transport 与 recovery Fact 的持久因果重建。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from src.app.execution.models import InboundEvidenceKind
from src.app.transport.contracts import (
    MoveRackRequest,
    RackPosition,
    RackReference,
    RcsTemplateId,
    TransportCaller,
    ZonePosition,
)
from src.app.wms_adapter.inbound_wire import parse_outbound_request, parse_outbound_response
from src.utils.timezone import timezone
from wes_plugin_sdk import (
    RecoveryDecidedFact as BaseRecoveryDecidedFact,
)
from wes_plugin_sdk import (
    RecoveryDecision,
    TransportRackPosition,
    TransportRackReference,
    TransportResultReadyFact,
    TransportZonePosition,
)

from rough_sorter.application.device_facts import completed_response
from rough_sorter.application.values import (
    COMMAND_SOURCE_PATTERN,
    bound_position,
    canonical_evidence_id,
    command_position,
    device_step,
    position_binding,
    rack_move_plan,
    required_position,
    required_string,
    stable_operation_id,
    strict_object,
    transport_rack_position,
)
from rough_sorter.application.wms_facts import validate_wms_execution
from rough_sorter.facts import (
    DeviceStep,
    RecoveryDecidedFact,
    RecoveryDeferContinuation,
    RecoveryDeviceContinuation,
    RecoveryWmsContinuation,
    TransportLeg,
    TransportOutcome,
    TransportOutcomePublishedFact,
)
from rough_sorter.wms_requests import wms_position

if TYPE_CHECKING:
    from src.app.execution.models import InboundEvidence, MaterialExecution
    from src.app.transport.repository import TransportRepository

    from rough_sorter.application.persistence import (
        DeviceCommandRepositoryPort,
        DeviceReadinessReader,
        EpochRepositoryPort,
        EvidenceRepositoryPort,
        RackPlacementRepositoryPort,
        RackPositionRepositoryPort,
        RackReplacementBindingRepositoryPort,
        WmsConfirmationRepositoryPort,
    )


def _core_rack_move_position(
    position: TransportRackReference | TransportZonePosition | TransportRackPosition,
) -> RackReference | ZonePosition | RackPosition:
    if type(position) is TransportRackReference:
        return RackReference(position.location_code)
    if type(position) is TransportZonePosition:
        return ZonePosition(position.location_code)
    if type(position) is TransportRackPosition:
        return RackPosition(position.location_code)
    raise TypeError("Transport position kind 非法")


async def current_rack_id(
    *,
    db: object,
    runtime: Any,
    rack_positions: RackPositionRepositoryPort,
    rack_placements: RackPlacementRepositoryPort,
) -> str:
    outlet = position_binding(runtime, "PIPELINE_OUTLET")
    rack_position = await rack_positions.get_by_workline_logic_location(
        db, workline_code=runtime.epoch.workline_code, logic_location_code=outlet.location_id
    )
    if rack_position is None or not rack_position.enabled:
        raise ValueError("PIPELINE_OUTLET 未精确关联 enabled WorklineRackPosition")
    placements = await rack_placements.list_active_by_workline_position(
        db, workline_code=runtime.epoch.workline_code, position_code=rack_position.position_code
    )
    if len(placements) != 1:
        raise ValueError("PIPELINE_OUTLET current rack missing or ambiguous")
    placement = placements[0]
    if placement.logic_location_code != outlet.location_id or placement.placement_status != "ARRIVED":
        raise ValueError("PIPELINE_OUTLET current rack projection 不确定")
    return required_string(placement.rack_code, "current_rack_id")


async def build_transport_fact(
    *,
    db: object,
    fact: TransportResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    evidences: EvidenceRepositoryPort,
    confirmations: WmsConfirmationRepositoryPort,
    bindings: RackReplacementBindingRepositoryPort,
    transport_tasks: TransportRepository,
) -> Any:
    payload = evidence.normalized_payload
    if (
        evidence.transport_task_id != fact.transport_task_id
        or payload.get("transport_task_id") != fact.transport_task_id
    ):
        raise ValueError("Transport fact/evidence task identity 不匹配")
    client_request_id = required_string(payload.get("client_request_id"), "client_request_id")
    binding = await bindings.get_by_client_request_id_for_update(db, client_request_id)
    if binding is None or binding.step != "NEW_IN":
        raise ValueError("material Transport fact 只接受持久 NEW_IN binding")
    if binding.line_run_epoch_id != execution.line_run_epoch_id:
        raise ValueError("NEW_IN binding Epoch correlation 不匹配")
    source = await evidences.get_by_id_for_update(db, binding.source_evidence_id)
    if (
        source is None
        or source.material_execution_id != execution.id
        or source.line_run_epoch_id != execution.line_run_epoch_id
        or source.operation != "inbound.source_rack.replacement_plan_decide@v1"
    ):
        raise ValueError("NEW_IN binding source evidence correlation 不匹配")
    source_operation_id = required_string(source.operation_id, "source.operation_id")
    confirmation = await confirmations.get_by_identity_for_update(db, source.operation, source_operation_id)
    if (
        confirmation is None
        or confirmation.material_execution_id != execution.id
        or confirmation.response_evidence_id != source.id
    ):
        raise ValueError("NEW_IN source confirmation correlation 不匹配")
    request = parse_outbound_request(confirmation.request_payload).model_dump(mode="json", exclude_none=True)
    if request.get("operation") != source.operation or request.get("operation_id") != source_operation_id:
        raise ValueError("NEW_IN source confirmation identity 不匹配")
    request_data = cast("dict[str, Any]", request["data"])
    validate_wms_execution(request_data, execution)
    if required_string(request_data.get("current_rack_id"), "current_rack_id") != binding.resource_fence_id:
        raise ValueError("NEW_IN binding current rack identity 不匹配")
    response = parse_outbound_response(source.operation, 200, source.normalized_payload).model_dump(
        mode="json", exclude_none=True
    )
    response_data = cast("dict[str, Any]", response["data"])
    if required_string(response_data.get("result"), "replacement.result") != "READY":
        raise ValueError("NEW_IN source evidence 未冻结 READY replacement plan")
    if required_string(response_data.get("rack_replacement_id"), "rack_replacement_id") != binding.correlation_id:
        raise ValueError("NEW_IN binding replacement identity 不匹配")
    old_plan = rack_move_plan(response_data.get("old_loaded_rack"))
    if old_plan.rack_id != binding.resource_fence_id:
        raise ValueError("NEW_IN binding old rack identity 不匹配")
    plan = rack_move_plan(response_data.get("new_empty_rack"))
    transport_task = await transport_tasks.get_task_by_client_request(cast("Any", db), client_request_id)
    source_position = _core_rack_move_position(plan.source)
    target_position = _core_rack_move_position(plan.target)
    expected_request = asdict(
        MoveRackRequest(
            client_request_id=client_request_id,
            caller=TransportCaller(workline_id=str(execution.workline_id)),
            rack_id=plan.rack_id,
            source=source_position,
            target=target_position,
            target_face=plan.target_face,
            rcs_template_id=RcsTemplateId.CTU01,
        )
    )
    if (
        transport_task is None
        or transport_task.transport_task_id != fact.transport_task_id
        or transport_task.client_request_id != client_request_id
        or transport_task.kind != "RACK_MOVE"
        or transport_task.request_json != expected_request
    ):
        raise ValueError("NEW_IN TransportTask frozen plan 与 WMS replacement plan 不匹配")
    status = required_string(payload.get("status"), "transport.status")
    outcome = {
        "SUCCEEDED": TransportOutcome.SUCCEEDED,
        "FAILED": TransportOutcome.FAILED,
        "REJECTED": TransportOutcome.FAILED,
        "UNKNOWN": TransportOutcome.UNKNOWN,
    }.get(status)
    if outcome is None:
        raise ValueError("Transport outcome status 非法")
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "evidence_id": fact.evidence_id,
        "fact_version": fact.fact_version,
        "material_execution_id": fact.material_execution_id,
        "transport_task_id": fact.transport_task_id,
        "runtime_snapshot": runtime,
        "material_trace_id": execution.material_trace_id,
        "rack_replacement_id": binding.correlation_id,
        "leg": TransportLeg.NEW_IN,
        "outcome": outcome,
        "rack_id": plan.rack_id,
        "expected_target": plan.target,
        "expected_face": plan.target_face,
    }
    if outcome is not TransportOutcome.SUCCEEDED:
        return TransportOutcomePublishedFact(
            **common, reason_code=required_string(payload.get("reason_code"), "transport.reason_code")
        )
    members = payload.get("members")
    if not isinstance(members, list) or len(members) != 1 or not isinstance(members[0], dict):
        raise ValueError("NEW_IN success 必须只有一个 rack member outcome")
    member = cast("dict[str, Any]", members[0])
    if member.get("position_unknown") is not False:
        raise ValueError("NEW_IN rack member position 不确定")
    final = transport_rack_position(member.get("final_position"))
    admission = await completed_response(
        db=db,
        execution=execution,
        operation="inbound.material.admission_decide@v1",
        required_result="ACCEPT",
        confirmations=confirmations,
        evidences=evidences,
    )
    if admission.get("result") != "ACCEPT":
        raise ValueError("NEW_IN target request 缺少已完成 admission ACCEPT")
    arrival_face = member.get("arrival_face")
    if type(arrival_face) is not str or arrival_face == "":
        raise ValueError("arrival_face 必须是非空 string")
    return TransportOutcomePublishedFact(
        **common,
        final_position=final,
        arrival_face=arrival_face,
        actual_rack_id=required_string(member.get("object_id"), "object_id"),
        source_position=bound_position(runtime, "PIPELINE_OUTLET", execution.material_trace_id),
        request_operation_id=client_request_id,
        pkg_id=required_string(admission.get("pkg_id"), "pkg_id"),
        inbound_admission_id=required_string(admission.get("inbound_admission_id"), "inbound_admission_id"),
    )


async def build_recovery_fact(
    *,
    db: object,
    fact: BaseRecoveryDecidedFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    evidences: EvidenceRepositoryPort,
    epochs: EpochRepositoryPort,
    commands: DeviceCommandRepositoryPort,
    readiness: DeviceReadinessReader,
    confirmations: WmsConfirmationRepositoryPort,
    rack_positions: RackPositionRepositoryPort,
    rack_placements: RackPlacementRepositoryPort,
) -> Any:
    if evidence.kind != InboundEvidenceKind.WMS_EVENT or evidence.operation != "inbound.execution.recovery_decided@v1":
        raise ValueError("Recovery Fact 必须引用 recovery_decided evidence")
    data = evidence.normalized_payload.get("data")
    if not isinstance(data, dict):
        raise TypeError("recovery evidence.data 缺失")
    causal_id = canonical_evidence_id(data.get("reconciling_evidence_id"), "reconciling_evidence_id")
    causal = await evidences.get_by_id_for_update(db, causal_id)
    if (
        causal is None
        or causal.material_execution_id != execution.id
        or causal.line_run_epoch_id != execution.line_run_epoch_id
    ):
        raise ValueError("recovery causal evidence correlation 不匹配")
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "evidence_id": fact.evidence_id,
        "fact_version": fact.fact_version,
        "material_execution_id": fact.material_execution_id,
        "recovery_id": fact.recovery_id,
        "decision": fact.decision,
        "authoritative_position": fact.authoritative_position,
        "reason_code": fact.reason_code,
        "runtime_snapshot": runtime,
        "material_trace_id": execution.material_trace_id,
        "reconciling_evidence_id": str(causal_id),
    }
    if fact.decision is RecoveryDecision.ABORT:
        return RecoveryDecidedFact(**common, continuation=None)
    authoritative = required_position(fact.authoritative_position, "authoritative_position")
    if causal.kind == InboundEvidenceKind.WMS_RESULT:
        operation = required_string(causal.operation, "causal.operation")
        confirmation = await confirmations.get_by_identity_for_update(
            db,
            operation,
            required_string(causal.operation_id, "causal.operation_id"),
        )
        if confirmation is None or confirmation.material_execution_id != execution.id:
            raise ValueError("Recovery causal WMS confirmation correlation 不匹配")
        request_data = parse_outbound_request(confirmation.request_payload).model_dump(mode="json", exclude_none=True)[
            "data"
        ]
        return RecoveryDecidedFact(
            **common,
            continuation=RecoveryWmsContinuation(
                operation=operation,
                operation_id=stable_operation_id(evidence, f"recovery:{operation}"),
                request_data=request_data,
            ),
        )
    if causal.kind == InboundEvidenceKind.DEVICE_RESULT:
        command_code = required_string(causal.command_code, "causal.command_code")
        command = await commands.get_by_command_code(db, command_code, for_update=True)
        if command is None or command.material_execution_id != execution.id or command.result_evidence_id != causal.id:
            raise ValueError("recovery causal DeviceCommand correlation 不匹配")
        params = strict_object(command.params, {"material_trace_id", "source", "target"}, "DeviceCommand params")
        source = command_position(params["source"], execution.material_trace_id)
        target = command_position(params["target"], execution.material_trace_id)
        step, role = device_step(command.task_type, source, target)
        if authoritative == source:
            binding = await epochs.get_binding_by_role_and_code_for_update(
                db,
                line_run_epoch_id=execution.line_run_epoch_id,
                device_role=role,
                device_code=command.device_code,
            )
            if binding is None:
                raise ValueError("recovery device binding missing")
            return RecoveryDecidedFact(
                **common,
                continuation=RecoveryDeviceContinuation(
                    device_role=role,
                    task_type=command.task_type,
                    source=source,
                    target=target,
                    device_ready=await readiness.is_ready(db, binding),
                ),
            )
        if authoritative != target:
            raise ValueError("authoritative position 不在 causal command frozen topology")
        operation = {
            DeviceStep.TRANSFER_TO_OUTLET: "inbound.material.target_decide@v1",
            DeviceStep.PLACEMENT_TO_CELL: "inbound.material.placement_report@v1",
            DeviceStep.MEASUREMENT_TO_NG: "inbound.material.ng_placement_report@v1",
            DeviceStep.PLACEMENT_TO_NG: "inbound.material.ng_placement_report@v1",
        }.get(step)
        if operation is None:
            return RecoveryDecidedFact(
                **common,
                continuation=RecoveryDeferContinuation(reason_code="RECOVERY_NEXT_DEVICE_REBUILD_REQUIRED"),
            )
        request_data: dict[str, Any]
        if operation == "inbound.material.target_decide@v1":
            admission = await completed_response(
                db=db,
                execution=execution,
                operation="inbound.material.admission_decide@v1",
                required_result="ACCEPT",
                confirmations=confirmations,
                evidences=evidences,
            )
            request_data = {
                "material_execution_id": execution.execution_code,
                "material_trace_id": execution.material_trace_id,
                "pkg_id": admission.get("pkg_id"),
                "inbound_admission_id": admission.get("inbound_admission_id"),
                "source_position": wms_position(target),
                "current_rack_id": await current_rack_id(
                    db=db,
                    runtime=runtime,
                    rack_positions=rack_positions,
                    rack_placements=rack_placements,
                ),
            }
        elif operation == "inbound.material.placement_report@v1":
            admission = await completed_response(
                db=db,
                execution=execution,
                operation="inbound.material.admission_decide@v1",
                required_result="ACCEPT",
                confirmations=confirmations,
                evidences=evidences,
            )
            assigned = await completed_response(
                db=db,
                execution=execution,
                operation="inbound.material.target_decide@v1",
                required_result="ASSIGNED",
                confirmations=confirmations,
                evidences=evidences,
            )
            request_data = {
                "material_execution_id": execution.execution_code,
                "material_trace_id": execution.material_trace_id,
                "pkg_id": admission.get("pkg_id"),
                "inbound_admission_id": admission.get("inbound_admission_id"),
                "target_assignment_id": assigned.get("target_assignment_id"),
                "target_position": wms_position(target),
                "placement_sequence": assigned.get("placement_sequence"),
                "command_code": command.command_code,
                "placed_at": int(timezone.to_utc(evidence.received_at).timestamp() * 1000),
            }
        else:
            source_match = COMMAND_SOURCE_PATTERN.fullmatch(command.execution_ref_id)
            source_evidence = (
                await evidences.get_by_id_for_update(db, int(source_match.group(1)))
                if source_match is not None
                else None
            )
            if source_evidence is None:
                raise ValueError("Recovery NG command source evidence missing")
            source_response = parse_outbound_response(
                required_string(source_evidence.operation, "source.operation"),
                200,
                source_evidence.normalized_payload,
            ).model_dump(mode="json", exclude_none=True)
            source_data = cast("dict[str, Any]", source_response["data"])
            request_data = {
                "material_execution_id": execution.execution_code,
                "material_trace_id": execution.material_trace_id,
                "ng_evidence_id": str(causal.id),
                "ng_position": wms_position(target),
                "reason_code": source_data.get("reason_code"),
                "business_context": "ROUGH_SORT_INBOUND",
            }
        return RecoveryDecidedFact(
            **common,
            continuation=RecoveryWmsContinuation(
                operation=operation,
                operation_id=stable_operation_id(evidence, f"recovery:{operation}"),
                request_data=request_data,
            ),
        )
    return RecoveryDecidedFact(
        **common, continuation=RecoveryDeferContinuation(reason_code="RECOVERY_CAUSAL_FACT_NOT_ACTIONABLE")
    )


__all__ = ["build_recovery_fact", "build_transport_fact", "current_rack_id"]
