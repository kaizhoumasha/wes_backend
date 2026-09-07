"""粗分机 WMS 结果 Fact 的持久因果重建。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.app.wms_adapter.inbound_material.typed import decode_request
from wes_plugin_sdk import (
    AdmissionAccepted,
    AdmissionIntent,
    AdmissionOutcome,
    FactRecorded,
    InboundWmsIntent,
    MaterialRejected,
    NgPlacementIntent,
    NgPlacementOutcome,
    NoAvailableCell,
    OperationWait,
    PlacementIntent,
    PlacementOutcome,
    ReplacementPlanIntent,
    ReplacementPlanOutcome,
    ReplacementReady,
    TargetAssigned,
    TargetIntent,
    TargetOutcome,
)

from rough_sorter.application.values import (
    bound_position,
    command_position,
    device_binding,
    position_binding,
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


def validate_wms_execution(data: InboundWmsIntent, execution: MaterialExecution) -> None:
    if data.material_execution_id != execution.execution_code or data.material_trace_id != execution.material_trace_id:
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
    request = decode_request(confirmation.request_payload, fact_id=fact.fact_id)
    if request.operation_id != operation_id:
        raise ValueError("WMS confirmation request identity 不匹配")
    if isinstance(request, AdmissionIntent):
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
    if isinstance(request, TargetIntent):
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
    if isinstance(request, (PlacementIntent, NgPlacementIntent)):
        return build_completion_fact(
            fact=fact, evidence=evidence, execution=execution, runtime=runtime, request=request
        )
    if isinstance(request, ReplacementPlanIntent):
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
    request: AdmissionIntent,
    epochs: EpochRepositoryPort,
    readiness: DeviceReadinessReader,
) -> Any:
    validate_wms_execution(request, execution)
    source = wire_position(request.source_position, execution.material_trace_id, "MEASUREMENT_POSITION")
    if source.location_id != position_binding(runtime, "MEASUREMENT_POSITION").location_id:
        raise ValueError("admission source position 与 Epoch binding 不匹配")
    if not isinstance(request, AdmissionIntent) or not isinstance(fact.outcome, AdmissionOutcome):
        raise ValueError("admission typed request/outcome mismatch")
    response_data = fact.outcome.result
    result = (
        AdmissionResult.ACCEPT
        if isinstance(response_data, AdmissionAccepted)
        else AdmissionResult.REJECT
        if isinstance(response_data, MaterialRejected)
        else AdmissionResult.WAIT
    )
    if not isinstance(response_data, (AdmissionAccepted, MaterialRejected, OperationWait)):
        raise ValueError("admission outcome is not an applicable business result")
    binding = device_binding(runtime, "MEASUREMENT_DEVICE")
    persisted = await epochs.get_binding_by_role_and_code_for_update(
        db,
        line_run_epoch_id=execution.line_run_epoch_id,
        device_role=binding.device_role,
        device_code=binding.device_code,
    )
    if persisted is None:
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
    if isinstance(response_data, AdmissionAccepted):
        return AdmissionDecidedFact(
            **common,
            pkg_id=response_data.pkg_id,
            inbound_admission_id=response_data.inbound_admission_id,
            next_position=bound_position(runtime, "PIPELINE_INLET", execution.material_trace_id),
        )
    if isinstance(response_data, MaterialRejected):
        destination = wire_position(response_data.ng_destination, execution.material_trace_id, "NG_POSITION")
        if destination.location_id != position_binding(runtime, "NG_POSITION").location_id:
            raise ValueError("WMS NG destination 与 Epoch binding 不匹配")
        return AdmissionDecidedFact(
            **common,
            reason_code=response_data.reason_code,
            next_position=destination,
        )
    return AdmissionDecidedFact(**common, reason_code=response_data.reason_code)


def build_completion_fact(
    *,
    fact: WmsResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    request: PlacementIntent | NgPlacementIntent,
) -> Any:
    validate_wms_execution(request, execution)
    if not isinstance(fact.outcome, (PlacementOutcome, NgPlacementOutcome)) or not isinstance(
        fact.outcome.result, FactRecorded
    ):
        raise ValueError("completion outcome is not a recorded business fact")
    if (isinstance(request, PlacementIntent) and not isinstance(fact.outcome, PlacementOutcome)) or (
        isinstance(request, NgPlacementIntent) and not isinstance(fact.outcome, NgPlacementOutcome)
    ):
        raise ValueError("completion typed request/outcome mismatch")
    result = CompletionResult.DUPLICATE if fact.outcome.result.duplicate else CompletionResult.RECORDED
    if isinstance(request, PlacementIntent):
        target = wire_position(request.target_position, execution.material_trace_id, "RACK_CELL")
        affected = (
            request.command_code,
            required_string(target.rack_id, "rack_id"),
            required_string(target.bin_cell_id, "bin_cell_id"),
        )
        kind = CompletionKind.PLACEMENT
    else:
        destination = wire_position(request.ng_position, execution.material_trace_id, "NG_POSITION")
        affected = (request.ng_evidence_id, destination.location_id)
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
    request: ReplacementPlanIntent,
    commands: DeviceCommandRepositoryPort,
    confirmations: WmsConfirmationRepositoryPort,
    rack_bindings: RackReplacementBindingRepositoryPort,
    current_rack_id: Any,
) -> Any:
    validate_wms_execution(request, execution)
    rack_id = request.current_rack_id
    if rack_id != await current_rack_id(db, runtime):
        raise ValueError("replacement request current rack 与 projection 不匹配")
    if not isinstance(request, ReplacementPlanIntent) or not isinstance(fact.outcome, ReplacementPlanOutcome):
        raise ValueError("replacement typed request/outcome mismatch")
    response_data = fact.outcome.result
    if not isinstance(response_data, (ReplacementReady, OperationWait)):
        raise ValueError("replacement outcome is not an applicable business result")
    result = ReplacementResult.READY if isinstance(response_data, ReplacementReady) else ReplacementResult.WAIT
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
    if isinstance(response_data, OperationWait):
        return ReplacementPlanDecidedFact(**common, reason_code=response_data.reason_code)
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
        rack_replacement_id=response_data.rack_replacement_id,
        old_loaded_rack=response_data.old_loaded_rack,
        new_empty_rack=response_data.new_empty_rack,
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
        request = decode_request(confirmation.request_payload, fact_id="rack-release")
        if not isinstance(request, PlacementIntent):
            raise ValueError("placement confirmation request type mismatch")
        command_code = request.command_code
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
    request: TargetIntent,
    epochs: EpochRepositoryPort,
    readiness: DeviceReadinessReader,
    rack_bindings: RackReplacementBindingRepositoryPort,
) -> Any:
    validate_wms_execution(request, execution)
    source = wire_position(request.source_position, execution.material_trace_id, "PIPELINE_OUTLET")
    if source.location_id != position_binding(runtime, "PIPELINE_OUTLET").location_id:
        raise ValueError("target source position 与 Epoch binding 不匹配")
    rack_id = request.current_rack_id
    if not isinstance(request, TargetIntent) or not isinstance(fact.outcome, TargetOutcome):
        raise ValueError("target typed request/outcome mismatch")
    response_data = fact.outcome.result
    if not isinstance(response_data, (TargetAssigned, NoAvailableCell, MaterialRejected, OperationWait)):
        raise ValueError("target outcome is not an applicable business result")
    result = (
        TargetResult.ASSIGNED
        if isinstance(response_data, TargetAssigned)
        else TargetResult.NO_AVAILABLE_CELL
        if isinstance(response_data, NoAvailableCell)
        else TargetResult.REJECT
        if isinstance(response_data, MaterialRejected)
        else TargetResult.WAIT
    )
    current_rack_fenced = False
    if isinstance(response_data, TargetAssigned):
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
    binding = device_binding(runtime, "PLACEMENT_DEVICE")
    persisted = await epochs.get_binding_by_role_and_code_for_update(
        db,
        line_run_epoch_id=execution.line_run_epoch_id,
        device_role=binding.device_role,
        device_code=binding.device_code,
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
    if isinstance(response_data, TargetAssigned):
        target = wire_position(response_data.target_position, execution.material_trace_id, "RACK_CELL")
        return TargetDecidedFact(
            **common,
            target_position=target,
            target_assignment_id=response_data.target_assignment_id,
            placement_sequence=response_data.placement_sequence,
            expected_height_mm=response_data.expected_height_mm,
        )
    if isinstance(response_data, NoAvailableCell):
        return TargetDecidedFact(
            **common,
            reason_code=response_data.reason_code,
            request_operation_id=fact.operation_id,
        )
    if isinstance(response_data, MaterialRejected):
        destination = wire_position(response_data.ng_destination, execution.material_trace_id, "NG_POSITION")
        if destination.location_id != position_binding(runtime, "NG_POSITION").location_id:
            raise ValueError("target reject NG destination 与 Epoch binding 不匹配")
        return TargetDecidedFact(
            **common,
            target_position=destination,
            reason_code=response_data.reason_code,
        )
    return TargetDecidedFact(**common, reason_code=response_data.reason_code)


__all__ = ["build_wms_fact"]
