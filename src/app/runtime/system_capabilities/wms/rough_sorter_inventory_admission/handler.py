"""粗分机 WMS 库存准入纯 QUERY handler。"""

from __future__ import annotations

from decimal import Decimal

from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.wms_integration.ports.inventory_query import (
    WmsInventoryItem,
    WmsInventoryQueryContractError,
    WmsInventoryQueryPort,
    WmsInventoryQueryRejected,
    WmsInventoryQueryUnavailable,
)

from .contracts import PROFILE_IDENTITY, RoughSorterInventoryAdmissionInput, RoughSorterInventoryAdmissionOutput

SOURCE_VERSION = "2026-07-06.material-flow"


class RoughSorterInventoryAdmissionHandler:
    """只依赖稳定 Port；匹配与输出转换均为本地纯计算。"""

    def __init__(self, inventory_port: WmsInventoryQueryPort) -> None:
        self._inventory_port = inventory_port

    async def __call__(self, request: RoughSorterInventoryAdmissionInput) -> object:
        if request.binding_snapshot.profile_identity != PROFILE_IDENTITY:
            return ContractViolation(error_code="WMS_PROFILE_MISMATCH", message="binding profile identity mismatch")
        try:
            inventory = await self._inventory_port.query_inventory(
                request.hhpn,
                warehouse_code=request.warehouse_code,
            )
        except (TimeoutError, WmsInventoryQueryUnavailable):
            return RetryableFailure(error_code="WMS_TIMEOUT", message="WMS inventory query unavailable")
        except WmsInventoryQueryRejected:
            return BusinessReject(reason_code="WMS_REJECTED", message="WMS inventory query rejected")
        except WmsInventoryQueryContractError:
            return ContractViolation(error_code="WMS_CONTRACT_INVALID", message="WMS inventory contract invalid")

        if not isinstance(inventory, list) or any(not isinstance(item, WmsInventoryItem) for item in inventory):
            return ContractViolation(error_code="WMS_CONTRACT_INVALID", message="WMS inventory contract invalid")
        matches = [
            item for item in inventory if item.material_code == request.hhpn and item.batch_no == request.lot_code
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
                available_quantity=sum((Decimal(str(item.quantity)) for item in matches), start=Decimal("0")),
                source_version=SOURCE_VERSION,
            )
        )


__all__ = ["SOURCE_VERSION", "RoughSorterInventoryAdmissionHandler"]
