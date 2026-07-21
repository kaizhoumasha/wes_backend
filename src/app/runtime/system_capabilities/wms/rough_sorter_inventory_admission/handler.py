"""粗分机 WMS 库存准入纯 QUERY handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryQueryOperationPort,
    InventoryQueryOperationRequest,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)

from .contracts import (
    SUPPORTED_PROFILE_IDENTITIES,
    RoughSorterInventoryAdmissionInput,
    RoughSorterInventoryAdmissionOutput,
)


def _translate_query_failure(outcome: object) -> object | None:
    """把封闭 QUERY 失败 outcome 转成 capability outcome。"""

    if isinstance(outcome, QueryBusinessReject):
        return BusinessReject(reason_code=outcome.reason_code, message=outcome.message)
    if isinstance(outcome, QueryTechnicalFailure):
        if outcome.retryable:
            return RetryableFailure(error_code=outcome.reason_code, message=outcome.message)
        return ContractViolation(error_code=outcome.reason_code, message=outcome.message)
    if isinstance(outcome, QueryContractFailure):
        return ContractViolation(error_code=outcome.reason_code, message=outcome.message)
    return None


class RoughSorterInventoryAdmissionHandler:
    """只依赖稳定 Port；匹配与输出转换均为本地纯计算。"""

    def __init__(self, inventory_port: InventoryQueryOperationPort) -> None:
        self._inventory_port = inventory_port

    async def __call__(self, request: RoughSorterInventoryAdmissionInput) -> object:
        if request.binding_snapshot.profile_identity not in SUPPORTED_PROFILE_IDENTITIES:
            return ContractViolation(error_code="WMS_PROFILE_MISMATCH", message="binding profile identity mismatch")
        outcome = await self._inventory_port.execute(
            InventoryQueryOperationRequest(
                material_code=request.hhpn,
                warehouse_code=request.warehouse_code,
                owner_code=request.owner_code,
                lot_no=request.lot_code,
            )
        )
        translated_failure = _translate_query_failure(outcome)
        if translated_failure is not None:
            return translated_failure

        if not isinstance(outcome, QuerySuccess):
            return ContractViolation(error_code="WMS_OUTCOME_INVALID", message="unknown WMS query outcome")
        inventory = outcome.value
        if inventory.source_version is None:
            return ContractViolation(error_code="WMS_SOURCE_VERSION_MISSING", message="WMS source version is required")
        matches = [
            item for item in inventory.items if item.material_code == request.hhpn and item.lot_no == request.lot_code
        ]
        if not matches:
            return BusinessReject(reason_code="WMS_REJECTED", message="WMS inventory did not match material and batch")
        return Success(
            payload=RoughSorterInventoryAdmissionOutput(
                accepted=True,
                material_code=request.hhpn,
                batch_no=request.lot_code,
                warehouse_code=request.warehouse_code,
                matched_item_count=len(matches),
                available_quantity=sum((item.available_quantity for item in matches), start=0),
                source_version=inventory.source_version,
            )
        )


__all__ = ["RoughSorterInventoryAdmissionHandler"]
