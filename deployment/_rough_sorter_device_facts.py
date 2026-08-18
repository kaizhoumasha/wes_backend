"""粗分机设备回调 Fact 的持久因果重建。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from deployment._rough_sorter_values import (
    COMMAND_SOURCE_PATTERN,
    bound_position,
    command_position,
    device_binding,
    device_step,
    positive_int,
    required_string,
    strict_object,
    validate_source_evidence_for_step,
    wire_position,
)
from src.app.device.contracts import EcsCommandResult, EcsCommandResultValue
from src.app.wms_adapter.inbound_wire import parse_outbound_response

if TYPE_CHECKING:
    from wes_plugin_sdk import DeviceResultReadyFact

    from deployment._rough_sorter_persistence import (
        DeviceCommandRepositoryPort,
        DeviceReadinessReader,
        EpochRepositoryPort,
        EvidenceRepositoryPort,
        WmsConfirmationRepositoryPort,
    )
    from deployment._rough_sorter_types import RoughSorterTypes
    from src.app.execution.models import InboundEvidence, MaterialExecution


async def completed_response(
    *,
    db: object,
    execution: MaterialExecution,
    operation: str,
    required_result: str,
    confirmations: WmsConfirmationRepositoryPort,
    evidences: EvidenceRepositoryPort,
) -> dict[str, Any]:
    if execution.id is None:
        raise ValueError("completed confirmation lookup requires persisted execution")
    records = await confirmations.list_for_execution(db, execution.id)
    matches = tuple(
        item
        for item in records
        if item.operation == operation
        and item.status == "COMPLETED"
        and item.response_result == required_result
        and item.response_evidence_id is not None
    )
    if len(matches) != 1:
        raise ValueError(f"execution completed {operation} confirmation missing or ambiguous")
    response_evidence = await evidences.get_by_id_for_update(db, cast("int", matches[0].response_evidence_id))
    if (
        response_evidence is None
        or response_evidence.operation != operation
        or response_evidence.operation_id != matches[0].operation_id
    ):
        raise ValueError("completed confirmation response evidence correlation 不匹配")
    response = parse_outbound_response(operation, 200, response_evidence.normalized_payload).model_dump(
        mode="json", exclude_none=True
    )
    return cast("dict[str, Any]", response["data"])


async def build_device_fact(
    *,
    db: object,
    fact: DeviceResultReadyFact,
    evidence: InboundEvidence,
    execution: MaterialExecution,
    runtime: Any,
    types: RoughSorterTypes,
    evidences: EvidenceRepositoryPort,
    epochs: EpochRepositoryPort,
    confirmations: WmsConfirmationRepositoryPort,
    commands: DeviceCommandRepositoryPort,
    readiness: DeviceReadinessReader,
    current_rack_id: Any,
) -> Any:
    command = await commands.get_by_command_code(db, fact.command_code, for_update=True)
    if (
        command is None
        or command.id is None
        or command.material_execution_id != execution.id
        or command.line_run_epoch_id != execution.line_run_epoch_id
        or command.device_code != fact.device_code
        or command.result_evidence_id != evidence.id
        or evidence.command_code != command.command_code
    ):
        raise ValueError("DeviceCommand/result evidence correlation 不匹配")
    source_match = COMMAND_SOURCE_PATTERN.fullmatch(command.execution_ref_id)
    if source_match is None or int(source_match.group(2)) != execution.id:
        raise ValueError("DeviceCommand execution_ref_id 不是当前 execution 的 canonical source evidence ref")
    source_evidence = await evidences.get_by_id_for_update(db, int(source_match.group(1)))
    if (
        source_evidence is None
        or source_evidence.material_execution_id != execution.id
        or source_evidence.line_run_epoch_id != execution.line_run_epoch_id
    ):
        raise ValueError("DeviceCommand source evidence correlation 不匹配")
    params = command.params
    if not isinstance(params, dict) or set(params) != {"material_trace_id", "source", "target"}:
        raise ValueError("DeviceCommand params 不是 rough sorter 严格闭集")
    if params.get("material_trace_id") != execution.material_trace_id:
        raise ValueError("DeviceCommand material_trace_id 不匹配")
    source = command_position(params.get("source"), execution.material_trace_id)
    target = command_position(params.get("target"), execution.material_trace_id)
    step, role = device_step(command.task_type, source, target, types)
    binding = device_binding(runtime, role)
    if (
        binding.device_code != command.device_code
        or binding.contract_key != command.contract_key
        or binding.contract_version != command.contract_version
    ):
        raise ValueError("DeviceCommand 与 Epoch device binding 不匹配")
    validate_source_evidence_for_step(source_evidence, step, types)
    result = EcsCommandResult.model_validate(evidence.normalized_payload)
    if (
        result.command_code != command.command_code
        or result.device_code != command.device_code
        or result.contract_key != command.contract_key
        or result.contract_version != command.contract_version
    ):
        raise ValueError("device result wire identity 不匹配")
    common: dict[str, Any] = {
        "fact_id": fact.fact_id,
        "evidence_id": fact.evidence_id,
        "fact_version": fact.fact_version,
        "material_execution_id": fact.material_execution_id,
        "command_code": fact.command_code,
        "device_code": fact.device_code,
        "material_trace_id": fact.material_trace_id,
        "runtime_snapshot": runtime,
        "step": step,
        "device_role": role,
        "source_position": source,
        "target_position": target,
    }
    if result.result is not EcsCommandResultValue.SUCCESS:
        reason = result.error_detail.code if result.error_detail is not None else "DEVICE_RESULT_FAILED"
        return types.DevicePositionConfirmedFact(**common, outcome=types.DeviceOutcome.FAILED, reason_code=reason)
    data = strict_object(result.data, {"material_trace_id", "actual_position"}, "device result data")
    if data["material_trace_id"] != execution.material_trace_id:
        raise ValueError("device result material_trace_id 不匹配")
    actual = command_position(data["actual_position"], execution.material_trace_id)
    if actual != target:
        raise ValueError("device result actual_position 与 frozen command target 不匹配")
    if step is types.DeviceStep.MEASUREMENT_TO_INLET:
        transfer = await epochs.get_binding_by_role_for_update(
            db, line_run_epoch_id=execution.line_run_epoch_id, device_role="TRANSFER_DEVICE"
        )
        if transfer is None:
            raise ValueError("transfer device binding missing")
        return types.DevicePositionConfirmedFact(
            **common,
            outcome=types.DeviceOutcome.SUCCESS,
            actual_position=actual,
            next_position=bound_position(runtime, "PIPELINE_OUTLET", execution.material_trace_id),
            next_device_ready=await readiness.is_ready(db, transfer),
        )
    if step is types.DeviceStep.TRANSFER_TO_OUTLET:
        admission_data = await completed_response(
            db=db,
            execution=execution,
            operation="inbound.material.admission_decide@v1",
            required_result="ACCEPT",
            confirmations=confirmations,
            evidences=evidences,
        )
        if admission_data.get("result") != "ACCEPT":
            raise ValueError("TRANSFER callback 缺少已完成 admission ACCEPT")
        return types.DevicePositionConfirmedFact(
            **common,
            outcome=types.DeviceOutcome.SUCCESS,
            actual_position=actual,
            request_operation_id=command.command_code,
            pkg_id=required_string(admission_data.get("pkg_id"), "pkg_id"),
            inbound_admission_id=required_string(admission_data.get("inbound_admission_id"), "inbound_admission_id"),
            current_rack_id=await current_rack_id(db, runtime),
        )
    if step is types.DeviceStep.PLACEMENT_TO_CELL:
        admission_data = await completed_response(
            db=db,
            execution=execution,
            operation="inbound.material.admission_decide@v1",
            required_result="ACCEPT",
            confirmations=confirmations,
            evidences=evidences,
        )
        target_data = await completed_response(
            db=db,
            execution=execution,
            operation="inbound.material.target_decide@v1",
            required_result="ASSIGNED",
            confirmations=confirmations,
            evidences=evidences,
        )
        if admission_data.get("result") != "ACCEPT" or target_data.get("result") != "ASSIGNED":
            raise ValueError("placement callback 缺少已完成 admission/target 决定")
        assigned_position = wire_position(target_data.get("target_position"), execution.material_trace_id, "RACK_CELL")
        if assigned_position != target:
            raise ValueError("placement command target 与 WMS assignment 不匹配")
        return types.DevicePositionConfirmedFact(
            **common,
            outcome=types.DeviceOutcome.SUCCESS,
            actual_position=actual,
            request_operation_id=command.command_code,
            pkg_id=required_string(admission_data.get("pkg_id"), "pkg_id"),
            inbound_admission_id=required_string(admission_data.get("inbound_admission_id"), "inbound_admission_id"),
            target_assignment_id=required_string(target_data.get("target_assignment_id"), "target_assignment_id"),
            placement_sequence=positive_int(target_data.get("placement_sequence"), "placement_sequence"),
            placed_at_ms=result.finish_time,
        )
    if step in {types.DeviceStep.MEASUREMENT_TO_NG, types.DeviceStep.PLACEMENT_TO_NG}:
        source_response = parse_outbound_response(
            required_string(source_evidence.operation, "source evidence operation"),
            200,
            source_evidence.normalized_payload,
        ).model_dump(mode="json", exclude_none=True)
        source_data = cast("dict[str, Any]", source_response["data"])
        if source_data.get("result") != "REJECT":
            raise ValueError("NG callback source evidence 必须是 WMS REJECT")
        destination = wire_position(source_data.get("ng_destination"), execution.material_trace_id, "NG_POSITION")
        if destination != target:
            raise ValueError("NG command target 与 WMS reject destination 不匹配")
        return types.DevicePositionConfirmedFact(
            **common,
            outcome=types.DeviceOutcome.SUCCESS,
            actual_position=actual,
            request_operation_id=command.command_code,
            ng_evidence_id=str(evidence.id),
            reason_code=required_string(source_data.get("reason_code"), "reason_code"),
        )
    raise ValueError(f"device result step 尚未装配: {step.value}")


__all__ = ["build_device_fact", "completed_response"]
