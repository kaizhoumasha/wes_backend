"""Registry QUERY Port 到 System Capability outcome 的统一薄适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort  # noqa: TC001
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


def _details(*, query_outcome_kind: str, evidence_key: str | None) -> dict[str, Any]:
    details: dict[str, Any] = {"query_outcome_kind": query_outcome_kind}
    if evidence_key is not None:
        details["provider_evidence_key"] = evidence_key
    return details


class WmsRegistryQueryCapabilityHandler:
    """只保留四类封闭 outcome；operation 解析完全委托静态 registry runtime。"""

    def __init__(self, query_port: WmsQueryExecutionPort) -> None:
        self._query_port = query_port

    async def __call__(self, request: BaseModel) -> object:
        outcome = await self._query_port.execute(request)
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
            message="WMS QUERY Port returned an unknown outcome",
            details={"query_outcome_kind": "INVALID"},
        )


__all__ = ["WmsRegistryQueryCapabilityHandler"]
