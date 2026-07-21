"""纯 admission decision 到当前 System Capability outcome 的边界适配。"""

from __future__ import annotations

from typing import cast

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterInventoryAdmissionDecision,
    RoughSorterInventoryQueryOutcomeKind,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success

from .contracts import RoughSorterInventoryAdmissionOutput


def to_runtime_outcome(decision: RoughSorterInventoryAdmissionDecision) -> object:
    """T7 删除专用 capability 前，将纯 decision 适配到现有 runtime outcome。"""

    source = decision.provenance.source
    details = {"admission_decision": decision.model_dump(mode="json")}
    message = source.message or decision.reason_code
    if decision.decision == "ADMIT":
        evidence = decision.evidence
        return Success(
            payload=RoughSorterInventoryAdmissionOutput(
                accepted=True,
                material_code=evidence.material_code,
                batch_no=evidence.lot_no,
                warehouse_code=evidence.warehouse_code,
                matched_item_count=evidence.matched_item_count,
                available_quantity=evidence.available_quantity,
                source_version=cast("str", source.source_version),
            )
        )
    if decision.decision == "REJECT":
        return BusinessReject(reason_code=decision.reason_code, message=message, details=details)
    if source.outcome_kind is RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE and source.retryable:
        return RetryableFailure(
            error_code=decision.reason_code,
            message=message,
            retry_after_seconds=source.retry_after_seconds,
            details=details,
        )
    return ContractViolation(error_code=decision.reason_code, message=message, details=details)


__all__ = ["to_runtime_outcome"]
