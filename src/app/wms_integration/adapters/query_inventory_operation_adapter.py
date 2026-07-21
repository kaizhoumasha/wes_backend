"""库存查询 Provider DTO 到领域合同的唯一 ACL 映射。"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic 运行时需要 Decimal。
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)

if TYPE_CHECKING:
    from src.app.runtime.system_capabilities.wms.contracts import WmsOperationContract
    from src.app.wms_integration.ports.query_outcome import WmsQueryOutcome
    from src.app.wms_integration.services.query_transport import WmsQueryTransportExecutor


class ProviderInventoryItemDTO(BaseModel):
    """当前 WMS Provider 的库存响应行；名称显式保留 transport 边界。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    sku: str = Field(min_length=1, max_length=120)
    available_qty: Decimal = Field(ge=0, allow_inf_nan=False)
    warehouse_code: str | None = Field(default=None, max_length=120)
    storage_location_code: str | None = Field(default=None, max_length=120)
    owner_code: str | None = Field(default=None, max_length=120)
    lot_no: str | None = Field(default=None, max_length=120)
    uom: str | None = Field(default=None, max_length=30)
    total_qty: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reserved_qty: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class ProviderQueryInventoryResponseDTO(BaseModel):
    """当前 WMS Provider 的库存响应。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    items: tuple[ProviderInventoryItemDTO, ...]
    source_version: str | None = Field(default=None, max_length=120)


def map_provider_query_inventory_response(raw_response: Any) -> InventoryQueryOperationResult:
    """在 adapter 边界完成唯一一次 Provider DTO → 领域快照映射。"""

    response = ProviderQueryInventoryResponseDTO.model_validate(raw_response)
    return InventoryQueryOperationResult(
        items=tuple(
            InventoryAuthorityItem(
                material_code=item.sku,
                available_quantity=item.available_qty,
                warehouse_code=item.warehouse_code,
                storage_location_code=item.storage_location_code,
                owner_code=item.owner_code,
                lot_no=item.lot_no,
                uom=item.uom,
                total_quantity=item.total_qty,
                reserved_quantity=item.reserved_qty,
            )
            for item in response.items
        ),
        source_version=response.source_version,
    )


class InventoryQueryOperationAdapter:
    """inventory operation 的唯一 Provider request/response DTO 映射边界。"""

    def __init__(self, *, executor: WmsQueryTransportExecutor, contract: WmsOperationContract) -> None:
        self._executor = executor
        self._contract = contract

    async def execute(
        self,
        request: InventoryQueryOperationRequest,
    ) -> WmsQueryOutcome[InventoryQueryOperationResult]:
        provider_payload = {
            "material_id": request.material_code,
            "warehouse_code": request.warehouse_code,
            "owner_code": request.owner_code,
            "lot_no": request.lot_no,
        }
        return await self._executor.execute(
            contract=self._contract,
            request=request,
            provider_payload={key: value for key, value in provider_payload.items() if value is not None},
            map_success=map_provider_query_inventory_response,
        )


__all__ = [
    "InventoryQueryOperationAdapter",
    "ProviderInventoryItemDTO",
    "ProviderQueryInventoryResponseDTO",
    "map_provider_query_inventory_response",
]
