"""WMS 同步 typed port 请求/响应模型。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WmsOperationName = Literal[
    "query_inventory",
    "reserve_inventory",
    "release_reservation",
    "confirm_inbound",
    "confirm_outbound",
]


class WmsPortRequest(BaseModel):
    """WMS 同步请求基础字段。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=120, description="调用方请求 ID")
    trace_id: str | None = Field(default=None, max_length=120, description="链路追踪 ID")


class WmsPortResponse(BaseModel):
    """WMS 同步响应基础字段。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str | None = Field(default=None, max_length=120, description="WMS 回传请求 ID")
    reason_code: str | None = Field(default=None, max_length=120, description="WMS 业务原因码")
    message: str | None = Field(default=None, max_length=500, description="WMS 响应消息")


class WmsInventoryItem(BaseModel):
    """WMS 库存查询行。"""

    model_config = ConfigDict(extra="ignore")

    sku: str = Field(min_length=1, max_length=120, description="物料编码")
    warehouse_code: str | None = Field(default=None, max_length=120, description="仓库编码")
    owner_code: str | None = Field(default=None, max_length=120, description="货主编码")
    lot_no: str | None = Field(default=None, max_length=120, description="批次号")
    uom: str | None = Field(default=None, max_length=30, description="计量单位")
    total_qty: Decimal = Field(default=Decimal("0"), ge=0, description="总库存数量")
    available_qty: Decimal = Field(default=Decimal("0"), ge=0, description="可用库存数量")
    reserved_qty: Decimal = Field(default=Decimal("0"), ge=0, description="已预留库存数量")


class QueryInventoryRequest(WmsPortRequest):
    """查询 WMS 库存。"""

    sku: str = Field(min_length=1, max_length=120, description="物料编码")
    warehouse_code: str | None = Field(default=None, max_length=120, description="仓库编码")
    owner_code: str | None = Field(default=None, max_length=120, description="货主编码")
    lot_no: str | None = Field(default=None, max_length=120, description="批次号")


class QueryInventoryResponse(WmsPortResponse):
    """WMS 库存查询结果。"""

    items: list[WmsInventoryItem] = Field(default_factory=list, description="库存行")


class ReserveInventoryRequest(WmsPortRequest):
    """预留 WMS 库存。"""

    reservation_key: str = Field(min_length=1, max_length=120, description="预留业务键")
    sku: str = Field(min_length=1, max_length=120, description="物料编码")
    qty: Decimal = Field(gt=0, description="预留数量")
    warehouse_code: str | None = Field(default=None, max_length=120, description="仓库编码")
    owner_code: str | None = Field(default=None, max_length=120, description="货主编码")
    lot_no: str | None = Field(default=None, max_length=120, description="批次号")


class ReserveInventoryResponse(WmsPortResponse):
    """WMS 库存预留结果。"""

    reservation_key: str = Field(min_length=1, max_length=120, description="预留业务键")
    accepted: bool = Field(description="WMS 是否接受预留")


class ReleaseReservationRequest(WmsPortRequest):
    """释放 WMS 库存预留。"""

    reservation_key: str = Field(min_length=1, max_length=120, description="预留业务键")
    reason: str | None = Field(default=None, max_length=240, description="释放原因")


class ReleaseReservationResponse(WmsPortResponse):
    """WMS 预留释放结果。"""

    reservation_key: str = Field(min_length=1, max_length=120, description="预留业务键")
    released: bool = Field(description="WMS 是否完成释放")


class ConfirmInboundRequest(WmsPortRequest):
    """确认 WMS 入库。"""

    inbound_key: str = Field(min_length=1, max_length=120, description="入库业务键")
    sku: str = Field(min_length=1, max_length=120, description="物料编码")
    qty: Decimal = Field(gt=0, description="入库数量")
    warehouse_code: str | None = Field(default=None, max_length=120, description="仓库编码")
    owner_code: str | None = Field(default=None, max_length=120, description="货主编码")
    lot_no: str | None = Field(default=None, max_length=120, description="批次号")


class ConfirmInboundResponse(WmsPortResponse):
    """WMS 入库确认结果。"""

    inbound_key: str = Field(min_length=1, max_length=120, description="入库业务键")
    confirmed: bool = Field(description="WMS 是否确认入库")


class ConfirmOutboundRequest(WmsPortRequest):
    """确认 WMS 出库。"""

    outbound_key: str = Field(min_length=1, max_length=120, description="出库业务键")
    sku: str = Field(min_length=1, max_length=120, description="物料编码")
    qty: Decimal = Field(gt=0, description="出库数量")
    warehouse_code: str | None = Field(default=None, max_length=120, description="仓库编码")
    owner_code: str | None = Field(default=None, max_length=120, description="货主编码")
    lot_no: str | None = Field(default=None, max_length=120, description="批次号")


class ConfirmOutboundResponse(WmsPortResponse):
    """WMS 出库确认结果。"""

    outbound_key: str = Field(min_length=1, max_length=120, description="出库业务键")
    confirmed: bool = Field(description="WMS 是否确认出库")


__all__ = [
    "ConfirmInboundRequest",
    "ConfirmInboundResponse",
    "ConfirmOutboundRequest",
    "ConfirmOutboundResponse",
    "QueryInventoryRequest",
    "QueryInventoryResponse",
    "ReleaseReservationRequest",
    "ReleaseReservationResponse",
    "ReserveInventoryRequest",
    "ReserveInventoryResponse",
    "WmsInventoryItem",
    "WmsOperationName",
    "WmsPortRequest",
    "WmsPortResponse",
]
