"""粗分机 WMS 结果 Fact 的持久因果重建。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.app.wms_adapter.inbound_wire import parse_outbound_request, parse_outbound_response

from rough_sorter.application.values import (
    bound_position,
    command_position,
    device_binding,
    position_binding,
    positive_int,
    rack_move_plan,
    required_string,
    wire_position,
)
from rough_sorter.facts import (
    AdmissionDecidedFact,
    AdmissionResult,
    CompletionKind,
    CompletionResult,
    PlacementCommandStatus,
    PlacementCompletedFact,
    PlacementConfirmationStatus,
    PlacementReleaseEvidence,
    PlacementResponseResult,
    RackReleaseSnapshot,
    ReplacementPlanDecidedFact,
    ReplacementResult,
    TargetDecidedFact,
    TargetResult,
    rack_release_snapshot_ref,
)

if TYPE_CHECKING:
    from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
    from wes_plugin_sdk import WmsResultReadyFact

    from rough_sorter.application.persistence import (
        DeviceCommandRepositoryPort,
        DeviceReadinessReader,
        EpochRepositoryPort,
        EvidenceRepositoryPort,
        RackReplacementBindingRepositoryPort,
        WmsConfirmationRepositoryPort,
    )


def validate_wms_execution(data: dict[str, Any], execution: MaterialExecution) -> None:
    if (
        data.get("material_execution_id") != execution.execution_code
        or data.get("material_trace_id") != execution.material_trace_id
    ):
        raise ValueError("WMS request execution identity 不匹配")


async def build_wms_fact(
    *,
    db: object,
    fact: WmsResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    evidences: EvidenceRepositoryPort,
    epochs: EpochRepositoryPort,
    confirmations: WmsConfirmationRepositoryPort,
    commands: DeviceCommandRepositoryPort,
    readiness: DeviceReadinessReader,
    rack_bindings: RackReplacementBindingRepositoryPort,
    current_rack_id: Any,
) -> Any:
    operation = required_string(evidence.operation, "evidence.operation")
    operation_id = required_string(evidence.operation_id, "evidence.operation_id")
    if fact.operation_id != operation_id:
        raise ValueError("WMS Fact operation_id 与 evidence 不匹配")
    confirmation = await confirmations.get_by_identity_for_update(db, operation, operation_id)
    if (
        confirmation is None
        or confirmation.material_execution_id != execution.id
        or confirmation.response_evidence_id != evidence.id
    ):
        raise ValueError("WMS confirmation/evidence correlation 不匹配")
    request = parse_outbound_request(confirmation.request_payload).model_dump(mode="json", exclude_none=True)
    if request.get("operation") != operation or request.get("operation_id") != operation_id:
        raise ValueError("WMS confirmation request identity 不匹配")
    if operation == "inbound.material.admission_decide@v1":
        return await build_admission_fact(
            db=db,
            fact=fact,
            evidence=evidence,
            execution=execution,
            runtime=runtime,
            request=request,
            epochs=epochs,
            readiness=readiness,
        )
    if operation == "inbound.material.target_decide@v1":
        return await build_target_fact(
            db=db,
            fact=fact,
            evidence=evidence,
            execution=execution,
            runtime=runtime,
            request=request,
            epochs=epochs,
            readiness=readiness,
            rack_bindings=rack_bindings,
        )
    if operation in {"inbound.material.placement_report@v1", "inbound.material.ng_placement_report@v1"}:
        return build_completion_fact(
            fact=fact, evidence=evidence, execution=execution, runtime=runtime, request=request
        )
    if operation == "inbound.source_rack.replacement_plan_decide@v1":
        return await build_replacement_fact(
            db=db,
            fact=fact,
            evidence=evidence,
            execution=execution,
            runtime=runtime,
            request=request,
            commands=commands,
            confirmations=confirmations,
            rack_bindings=rack_bindings,
            current_rack_id=current_rack_id,
        )
    raise ValueError(f"rough sorter 不支持 WMS operation: {operation}")


async def build_admission_fact(
    *,
    db: object,
    fact: WmsResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    request: dict[str, Any],
    epochs: EpochRepositoryPort,
    readiness: DeviceReadinessReader,
) -> Any:
    request_data = cast("dict[str, Any]", request["data"])
    validate_wms_execution(request_data, execution)
    source = wire_position(request_data["source_position"], execution.material_trace_id, "MEASUREMENT_POSITION")
    if source.location_id != position_binding(runtime, "MEASUREMENT_POSITION").location_id:
        raise ValueError("admission source position 与 Epoch binding 不匹配")
    response = parse_outbound_response(
        "inbound.material.admission_decide@v1", 200, evidence.normalized_payload
    ).model_dump(mode="json", exclude_none=True)
    response_data = cast("dict[str, Any]", response["data"])
    result = AdmissionResult(required_string(response_data.get("result"), "admission.result"))
    binding = device_binding(runtime, "MEASUREMENT_DEVICE")
    persisted = await epochs.get_binding_by_role_for_update(
        db, line_run_epoch_id=execution.line_run_epoch_id, device_role=binding.device_role
    )
    if persisted is None or persisted.device_code != binding.device_code:
        raise ValueError("measurement device binding drift")
    device_ready = await readiness.is_ready(db, persisted)
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "evidence_id": fact.evidence_id,
        "fact_version": fact.fact_version,
        "material_execution_id": fact.material_execution_id,
        "operation_id": fact.operation_id,
        "runtime_snapshot": runtime,
        "material_trace_id": execution.material_trace_id,
        "result": result,
        "source_position": source,
        "device_ready": device_ready,
    }
    if result is AdmissionResult.ACCEPT:
        return AdmissionDecidedFact(
            **common,
            pkg_id=required_string(response_data.get("pkg_id"), "pkg_id"),
            inbound_admission_id=required_string(response_data.get("inbound_admission_id"), "inbound_admission_id"),
            next_position=bound_position(runtime, "PIPELINE_INLET", execution.material_trace_id),
        )
    if result is AdmissionResult.REJECT:
        destination = wire_position(response_data.get("ng_destination"), execution.material_trace_id, "NG_POSITION")
        if destination.location_id != position_binding(runtime, "NG_POSITION").location_id:
            raise ValueError("WMS NG destination 与 Epoch binding 不匹配")
        return AdmissionDecidedFact(
            **common,
            reason_code=required_string(response_data.get("reason_code"), "reason_code"),
            next_position=destination,
        )
    return AdmissionDecidedFact(**common, reason_code=required_string(response_data.get("reason_code"), "reason_code"))


def build_completion_fact(
    *,
    fact: WmsResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    request: dict[str, Any],
) -> Any:
    request_data = cast("dict[str, Any]", request["data"])
    validate_wms_execution(request_data, execution)
    response = parse_outbound_response(
        required_string(evidence.operation, "evidence.operation"), 200, evidence.normalized_payload
    ).model_dump(mode="json", exclude_none=True)
    result = CompletionResult(required_string(response.get("code"), "completion code"))
    if evidence.operation == "inbound.material.placement_report@v1":
        target = wire_position(request_data.get("target_position"), execution.material_trace_id, "RACK_CELL")
        affected = (
            required_string(request_data.get("command_code"), "command_code"),
            required_string(target.rack_id, "rack_id"),
            required_string(target.bin_cell_id, "bin_cell_id"),
        )
        kind = CompletionKind.PLACEMENT
    else:
        destination = wire_position(request_data.get("ng_position"), execution.material_trace_id, "NG_POSITION")
        affected = (required_string(request_data.get("ng_evidence_id"), "ng_evidence_id"), destination.location_id)
        kind = CompletionKind.NG_PLACEMENT
    return PlacementCompletedFact(
        fact_id=fact.fact_id,
        evidence_id=fact.evidence_id,
        fact_version=fact.fact_version,
        material_execution_id=fact.material_execution_id,
        operation_id=fact.operation_id,
        runtime_snapshot=runtime,
        material_trace_id=execution.material_trace_id,
        kind=kind,
        result=result,
        affected_resource_ids=affected,
    )


async def build_replacement_fact(
    *,
    db: object,
    fact: WmsResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    request: dict[str, Any],
    commands: DeviceCommandRepositoryPort,
    confirmations: WmsConfirmationRepositoryPort,
    rack_bindings: RackReplacementBindingRepositoryPort,
    current_rack_id: Any,
) -> Any:
    request_data = cast("dict[str, Any]", request["data"])
    validate_wms_execution(request_data, execution)
    rack_id = required_string(request_data.get("current_rack_id"), "current_rack_id")
    if rack_id != await current_rack_id(db, runtime):
        raise ValueError("replacement request current rack 与 projection 不匹配")
    response = parse_outbound_response(
        "inbound.source_rack.replacement_plan_decide@v1", 200, evidence.normalized_payload
    ).model_dump(mode="json", exclude_none=True)
    response_data = cast("dict[str, Any]", response["data"])
    result = ReplacementResult(required_string(response_data.get("result"), "replacement.result"))
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "evidence_id": fact.evidence_id,
        "fact_version": fact.fact_version,
        "material_execution_id": fact.material_execution_id,
        "operation_id": fact.operation_id,
        "runtime_snapshot": runtime,
        "material_trace_id": execution.material_trace_id,
        "result": result,
        "current_rack_id": rack_id,
    }
    if result is not ReplacementResult.READY:
        return ReplacementPlanDecidedFact(
            **common, reason_code=required_string(response_data.get("reason_code"), "reason_code")
        )
    await rack_bindings.lock_resource_fence(
        db,
        line_run_epoch_id=execution.line_run_epoch_id,
        resource_fence_id=rack_id,
    )
    release = await rack_release_snapshot(
        db=db,
        execution=execution,
        current_rack_id=rack_id,
        commands=commands,
        confirmations=confirmations,
    )
    return ReplacementPlanDecidedFact(
        **common,
        release_snapshot=release,
        rack_replacement_id=required_string(response_data.get("rack_replacement_id"), "rack_replacement_id"),
        old_loaded_rack=rack_move_plan(response_data.get("old_loaded_rack")),
        new_empty_rack=rack_move_plan(response_data.get("new_empty_rack")),
    )


async def rack_release_snapshot(
    *,
    db: object,
    execution: MaterialExecution,
    current_rack_id: str,
    commands: DeviceCommandRepositoryPort,
    confirmations: WmsConfirmationRepositoryPort,
) -> Any:
    if execution.id is None:
        raise ValueError("rack release requires persisted execution")
    command_records = await commands.list_for_epoch_for_update(db, line_run_epoch_id=execution.line_run_epoch_id)
    rack_commands: list[Any] = []
    for command in command_records:
        if command.task_type != "PICK_AND_PUT":
            continue
        material_trace_id = required_string(command.params.get("material_trace_id"), "placement material_trace_id")
        target = command_position(command.params.get("target"), material_trace_id)
        if target.location_type != "RACK_CELL" or target.rack_id != current_rack_id:
            continue
        if command.material_execution_id is None:
            raise ValueError("placement command missing material execution correlation")
        rack_commands.append(command)
    execution_ids = tuple(sorted({cast("int", command.material_execution_id) for command in rack_commands}))
    confirmation_records = await confirmations.list_for_executions_for_update(
        db,
        material_execution_ids=execution_ids,
        operation="inbound.material.placement_report@v1",
    )
    placement_confirmations: dict[str, WmsConfirmation] = {}
    for confirmation in confirmation_records:
        if confirmation.operation != "inbound.material.placement_report@v1":
            continue
        request = parse_outbound_request(confirmation.request_payload).model_dump(mode="json", exclude_none=True)
        command_code = required_string(
            cast("dict[str, Any]", request["data"]).get("command_code"), "placement command_code"
        )
        if command_code in placement_confirmations:
            raise ValueError("duplicate placement confirmation command correlation")
        placement_confirmations[command_code] = confirmation
    items: list[Any] = []
    for command in rack_commands:
        confirmation = placement_confirmations.get(command.command_code)
        if confirmation is None:
            items.append(
                PlacementReleaseEvidence(
                    command_code=command.command_code,
                    command_status=PlacementCommandStatus(command.status),
                    command_result_evidence_id=command.result_evidence_id,
                    confirmation_operation=None,
                    confirmation_operation_id=None,
                    confirmation_status=PlacementConfirmationStatus.ABSENT,
                    response_result=None,
                    response_evidence_id=None,
                )
            )
            continue
        if confirmation.material_execution_id != command.material_execution_id:
            raise ValueError("placement confirmation execution correlation mismatch")
        items.append(
            PlacementReleaseEvidence(
                command_code=command.command_code,
                command_status=PlacementCommandStatus(command.status),
                command_result_evidence_id=command.result_evidence_id,
                confirmation_operation=confirmation.operation,
                confirmation_operation_id=confirmation.operation_id,
                confirmation_status=PlacementConfirmationStatus(confirmation.status),
                response_result=(
                    PlacementResponseResult(confirmation.response_result)
                    if confirmation.response_result is not None
                    else None
                ),
                response_evidence_id=confirmation.response_evidence_id,
            )
        )
    placements = tuple(sorted(items, key=lambda item: item.command_code))
    return RackReleaseSnapshot(
        current_rack_id=current_rack_id,
        placements=placements,
        snapshot_ref=rack_release_snapshot_ref(current_rack_id, placements),
    )


async def build_target_fact(
    *,
    db: object,
    fact: WmsResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    request: dict[str, Any],
    epochs: EpochRepositoryPort,
    readiness: DeviceReadinessReader,
    rack_bindings: RackReplacementBindingRepositoryPort,
) -> Any:
    request_data = cast("dict[str, Any]", request["data"])
    validate_wms_execution(request_data, execution)
    source = wire_position(request_data["source_position"], execution.material_trace_id, "PIPELINE_OUTLET")
    if source.location_id != position_binding(runtime, "PIPELINE_OUTLET").location_id:
        raise ValueError("target source position 与 Epoch binding 不匹配")
    rack_id = required_string(request_data.get("current_rack_id"), "current_rack_id")
    response = parse_outbound_response(
        "inbound.material.target_decide@v1", 200, evidence.normalized_payload
    ).model_dump(mode="json", exclude_none=True)
    response_data = cast("dict[str, Any]", response["data"])
    result = TargetResult(required_string(response_data.get("result"), "target.result"))
    current_rack_fenced = False
    if result is TargetResult.ASSIGNED:
        await rack_bindings.lock_resource_fence(
            db,
            line_run_epoch_id=execution.line_run_epoch_id,
            resource_fence_id=rack_id,
        )
        current_rack_fenced = (
            await rack_bindings.get_by_resource_step_for_update(
                db,
                line_run_epoch_id=execution.line_run_epoch_id,
                resource_fence_id=rack_id,
                step="OLD_OUT",
            )
            is not None
        )
    persisted = await epochs.get_binding_by_role_for_update(
        db, line_run_epoch_id=execution.line_run_epoch_id, device_role="PLACEMENT_DEVICE"
    )
    if persisted is None:
        raise ValueError("placement device binding missing")
    device_ready = await readiness.is_ready(db, persisted)
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "evidence_id": fact.evidence_id,
        "fact_version": fact.fact_version,
        "material_execution_id": fact.material_execution_id,
        "operation_id": fact.operation_id,
        "runtime_snapshot": runtime,
        "material_trace_id": execution.material_trace_id,
        "result": result,
        "source_position": source,
        "current_rack_id": rack_id,
        "current_rack_fenced": current_rack_fenced,
        "device_ready": device_ready,
    }
    if result is TargetResult.ASSIGNED:
        target = wire_position(response_data.get("target_position"), execution.material_trace_id, "RACK_CELL")
        return TargetDecidedFact(
            **common,
            target_position=target,
            target_assignment_id=required_string(response_data.get("target_assignment_id"), "target_assignment_id"),
            placement_sequence=positive_int(response_data.get("placement_sequence"), "placement_sequence"),
            expected_height_mm=required_string(response_data.get("expected_height_mm"), "expected_height_mm"),
        )
    if result is TargetResult.NO_AVAILABLE_CELL:
        return TargetDecidedFact(
            **common,
            reason_code=required_string(response_data.get("reason_code"), "reason_code"),
            request_operation_id=fact.operation_id,
        )
    if result is TargetResult.REJECT:
        destination = wire_position(response_data.get("ng_destination"), execution.material_trace_id, "NG_POSITION")
        if destination.location_id != position_binding(runtime, "NG_POSITION").location_id:
            raise ValueError("target reject NG destination 与 Epoch binding 不匹配")
        return TargetDecidedFact(
            **common,
            target_position=destination,
            reason_code=required_string(response_data.get("reason_code"), "reason_code"),
        )
    return TargetDecidedFact(**common, reason_code=required_string(response_data.get("reason_code"), "reason_code"))


__all__ = ["build_wms_fact"]
