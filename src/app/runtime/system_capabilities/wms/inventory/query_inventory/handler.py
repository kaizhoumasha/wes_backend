"""通用 inventory QUERY Port 到 System Capability outcome 的薄适配。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.wms_integration.ports.query_inventory_operation import (  # noqa: TC001 - index builder 会在运行时解析 handler 注解。
    InventoryQueryOperationPort,
    InventoryQueryOperationRequest,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)


def _details(*, query_outcome_kind: str, evidence_key: str | None) -> dict[str, Any]:
    details: dict[str, Any] = {"query_outcome_kind": query_outcome_kind}
    if evidence_key is not None:
        details["provider_evidence_key"] = evidence_key
    return details


class InventoryQueryCapabilityHandler:
    """只执行 typed QUERY 并保持四类领域 outcome；不包含业务 admission。"""

    def __init__(self, inventory_port: InventoryQueryOperationPort) -> None:
        self._inventory_port = inventory_port

    async def __call__(self, request: InventoryQueryOperationRequest) -> object:
        outcome = await self._inventory_port.execute(request)
        if isinstance(outcome, QuerySuccess):
            return Success(payload=outcome.value)
        if isinstance(outcome, QueryBusinessReject):
            return BusinessReject(
                reason_code=outcome.reason_code,
                message=outcome.message,
                details=_details(query_outcome_kind="BUSINESS_REJECT", evidence_key=outcome.evidence_key),
            )
        if isinstance(outcome, QueryTechnicalFailure):
            details = _details(query_outcome_kind="TECHNICAL_FAILURE", evidence_key=outcome.evidence_key)
            if outcome.retryable:
                return RetryableFailure(
                    error_code=outcome.reason_code,
                    message=outcome.message,
                    retry_after_seconds=outcome.retry_after_seconds,
                    details=details,
                )
            return ContractViolation(error_code=outcome.reason_code, message=outcome.message, details=details)
        if isinstance(outcome, QueryContractFailure):
            return ContractViolation(
                error_code=outcome.reason_code,
                message=outcome.message,
                details=_details(query_outcome_kind="CONTRACT_FAILURE", evidence_key=outcome.evidence_key),
            )
        return ContractViolation(
            error_code="QUERY_OUTCOME_INVALID",
            message="inventory QUERY Port returned an unknown outcome",
            details={"query_outcome_kind": "INVALID"},
        )


__all__ = ["InventoryQueryCapabilityHandler"]
