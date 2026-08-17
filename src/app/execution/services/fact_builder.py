"""把已完成基础处理的规范化 evidence 转为 SDK Fact 引用。"""

from __future__ import annotations

from wes_plugin_sdk import (
    DevicePosition,
    DeviceResultReadyFact,
    EvidenceReadyFact,
    FactReference,
    RecoveryDecidedFact,
    RecoveryDecision,
    TransportResultReadyFact,
    WmsResultReadyFact,
)

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
)


class FactBuilder:
    """只从持久化身份和已关联对象构建 Fact，不解释供应商私有字段。"""

    def build(
        self,
        evidence: InboundEvidence,
        execution: MaterialExecution,
        *,
        causal_evidence: InboundEvidence | None = None,
    ) -> FactReference:
        self._validate_correlations(evidence, execution)
        fact_id = f"evidence:{evidence.id}"
        common: dict[str, str] = {
            "fact_id": fact_id,
            "evidence_id": str(evidence.id),
            "fact_version": _required(evidence.contract_version, "contract_version"),
            "material_execution_id": execution.execution_code,
        }
        kind = InboundEvidenceKind(evidence.kind)
        if kind is InboundEvidenceKind.DEVICE_EVENT:
            return EvidenceReadyFact(**common)
        if kind is InboundEvidenceKind.DEVICE_RESULT:
            return DeviceResultReadyFact(
                **common,
                command_code=_required(evidence.command_code, "command_code"),
                device_code=_required(evidence.device_code, "device_code"),
                material_trace_id=execution.material_trace_id,
            )
        if kind is InboundEvidenceKind.WMS_RESULT:
            return WmsResultReadyFact(
                **common,
                operation_id=_required(evidence.operation_id, "operation_id"),
            )
        if kind is InboundEvidenceKind.TRANSPORT_RESULT:
            self._validate_transport_causal(evidence, execution, causal_evidence)
            return TransportResultReadyFact(
                **common,
                transport_task_id=_required(evidence.transport_task_id, "transport_task_id"),
            )
        if kind is InboundEvidenceKind.WMS_EVENT:
            return self._build_recovery(evidence, execution, common)
        raise ValueError(f"不支持的 InboundEvidence kind: {kind}")

    @staticmethod
    def _build_recovery(
        evidence: InboundEvidence,
        execution: MaterialExecution,
        common: dict[str, str],
    ) -> RecoveryDecidedFact:
        if evidence.operation != "inbound.execution.recovery_decided@v1":
            raise ValueError("WMS_EVENT 只接受 recovery_decided operation")
        data = evidence.normalized_payload.get("data")
        if not isinstance(data, dict):
            raise TypeError("recovery evidence 缺少 data")
        if (
            MaterialExecutionStatus(execution.status) is not MaterialExecutionStatus.RECONCILING
            or data.get("material_execution_id") != execution.execution_code
            or data.get("material_trace_id") != execution.material_trace_id
            or data.get("reconciling_evidence_id") != str(execution.last_transition_evidence_id)
        ):
            raise ValueError("reconciling_evidence_id does not match the current execution fence")
        decision = RecoveryDecision(_required_string(data.get("decision"), "decision"))
        return RecoveryDecidedFact(
            **common,
            recovery_id=_required_string(data.get("recovery_id"), "recovery_id"),
            decision=decision,
            authoritative_position=_device_position(data.get("authoritative_position"), execution.material_trace_id),
            reason_code=_required_string(data.get("reason_code"), "reason_code"),
        )

    @staticmethod
    def _validate_transport_causal(
        evidence: InboundEvidence,
        execution: MaterialExecution,
        causal_evidence: InboundEvidence | None,
    ) -> None:
        if MaterialExecutionStatus(execution.status) is not MaterialExecutionStatus.RECONCILING:
            return
        current_version = _positive_int(evidence.normalized_payload.get("outcome_version"), "outcome_version")
        current_status = evidence.normalized_payload.get("status")
        if current_status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("RECONCILING transport recovery requires a determinate outcome")
        if (
            causal_evidence is None
            or causal_evidence.id != execution.last_transition_evidence_id
            or causal_evidence.kind != InboundEvidenceKind.TRANSPORT_RESULT
            or causal_evidence.transport_task_id != evidence.transport_task_id
            or causal_evidence.normalized_payload.get("status") != "UNKNOWN"
            or _positive_int(causal_evidence.normalized_payload.get("outcome_version"), "causal outcome_version")
            >= current_version
        ):
            raise ValueError("transport recovery causal evidence does not match the current UNKNOWN fence")

    @staticmethod
    def _validate_correlations(evidence: InboundEvidence, execution: MaterialExecution) -> None:
        if evidence.id is None or execution.id is None:
            raise ValueError("Fact 只能引用已持久化 evidence 和 MaterialExecution")
        if evidence.apply_status != InboundEvidenceApplyStatus.APPLIED:
            raise ValueError("Fact 只能由已完成基础处理的 evidence 构建")
        if evidence.material_execution_id != execution.id:
            raise ValueError("evidence 与 MaterialExecution 关联不匹配")
        if evidence.line_run_epoch_id != execution.line_run_epoch_id:
            raise ValueError("evidence 与 MaterialExecution Epoch 不匹配")


def _required(value: str | None, field_name: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"Fact 缺少已验证关联字段: {field_name}")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _required_string(value: object, field_name: str) -> str:
    return _required(value if isinstance(value, str) else None, field_name)


def _device_position(value: object, material_trace_id: str) -> DevicePosition | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("authoritative_position must be an object or null")
    location_type = _required_string(value.get("type"), "authoritative_position.type")
    location_id_value = (
        value.get("location_code") if location_type != "ONE_LAYER_BIN_CELL" else value.get("bin_cell_id")
    )
    return DevicePosition(
        location_id=_required_string(location_id_value, "authoritative_position identity"),
        location_type=location_type,
        material_trace_id=material_trace_id,
        rack_id=value.get("rack_id") if isinstance(value.get("rack_id"), str) else None,
        rack_slot_code=value.get("rack_slot_code") if isinstance(value.get("rack_slot_code"), str) else None,
        bin_id=value.get("bin_id") if isinstance(value.get("bin_id"), str) else None,
        bin_cell_id=value.get("bin_cell_id") if isinstance(value.get("bin_cell_id"), str) else None,
    )


__all__ = ["FactBuilder"]
