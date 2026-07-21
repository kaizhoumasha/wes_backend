"""库存查询 operation 的唯一领域合同。"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic 运行时需要 Decimal。
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

OPERATION_IDENTITY = "wms.inventory.query_inventory@v1"
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InventoryQueryOperationRequest(BaseModel):
    """查询库存所需的领域过滤条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: StableText = Field(max_length=120)
    warehouse_code: StableText | None = Field(default=None, max_length=120)
    owner_code: StableText | None = Field(default=None, max_length=120)
    lot_no: StableText | None = Field(default=None, max_length=120)


class InventoryAuthorityItem(BaseModel):
    """Provider 事实映射后的库存权威行；缺失事实保持 None。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: StableText = Field(max_length=120)
    available_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    warehouse_code: StableText | None = Field(default=None, max_length=120)
    storage_location_code: StableText | None = Field(default=None, max_length=120)
    owner_code: StableText | None = Field(default=None, max_length=120)
    lot_no: StableText | None = Field(default=None, max_length=120)
    uom: StableText | None = Field(default=None, max_length=30)
    total_quantity: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reserved_quantity: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class InventoryQueryOperationResult(BaseModel):
    """库存查询成功返回的权威快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[InventoryAuthorityItem, ...]
    source_version: StableText | None = Field(default=None, max_length=120)


class InventoryQueryOperationPort(Protocol):
    """按单个 operation 暴露的稳定查询 Port。"""

    async def execute(self, request: InventoryQueryOperationRequest) -> InventoryQueryOperationResult: ...


__all__ = [
    "OPERATION_IDENTITY",
    "InventoryAuthorityItem",
    "InventoryQueryOperationPort",
    "InventoryQueryOperationRequest",
    "InventoryQueryOperationResult",
]
