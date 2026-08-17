"""把已完成基础处理的规范化 evidence 转为 SDK Fact 引用。"""

from __future__ import annotations

from typing import Any

from wes_plugin_sdk import (
    DeviceResultReadyFact,
    EvidenceReadyFact,
    FactReference,
    ReconciliationResultReadyFact,
    WmsResultReadyFact,
)

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
)


class FactBuilder:
    """只从持久化身份和已关联对象构建 Fact，不解释供应商私有字段。"""

    def build(self, evidence: InboundEvidence, execution: MaterialExecution) -> FactReference:
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
        if kind is InboundEvidenceKind.WMS_EVENT:
            return ReconciliationResultReadyFact(
                **common,
                reconciliation_id=_reconciliation_id(evidence.normalized_payload),
            )
        raise ValueError(f"不支持的 InboundEvidence kind: {kind}")

    def build_reconciliation(
        self,
        evidence: InboundEvidence,
        execution: MaterialExecution,
    ) -> ReconciliationResultReadyFact:
        if evidence.id is None or execution.id is None:
            raise ValueError("Fact 只能引用已持久化 evidence 和 MaterialExecution")
        if evidence.kind != InboundEvidenceKind.WMS_EVENT:
            raise ValueError("批量 evidence binding 只用于 WMS reconciliation event")
        if evidence.apply_status != InboundEvidenceApplyStatus.APPLIED:
            raise ValueError("Fact 只能由已完成基础处理的 evidence 构建")
        return ReconciliationResultReadyFact(
            fact_id=f"evidence:{evidence.id}:execution:{execution.execution_code}",
            evidence_id=str(evidence.id),
            fact_version=_required(evidence.contract_version, "contract_version"),
            material_execution_id=execution.execution_code,
            reconciliation_id=_reconciliation_id(evidence.normalized_payload),
        )

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


def _reconciliation_id(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TypeError("WMS reconciliation evidence 缺少 data")
    value = data.get("reconciliation_id")
    return _required(value if isinstance(value, str) else None, "reconciliation_id")


__all__ = ["FactBuilder"]
