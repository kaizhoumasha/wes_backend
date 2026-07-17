"""WmsInventoryQueryPort。

主计划 §5.1 7 port 之一: 库存只读查询 (query_inventory, query_empty_bins)。
由 typed_ports.WmsInventoryPort 拆 query 部分; transaction 部分迁至
WmsInventoryTransactionPort。所有方法 query-only, 短 TTL 缓存 30s。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsInventoryItem(BaseModel):
    """库存项（复用 typed_ports.WmsInventoryItem schema）。"""

    model_config = ConfigDict(extra="forbid")

    material_code: str = Field(min_length=1, max_length=80, description="物料编码")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")
    storage_location_code: str = Field(description="库位编码")
    quantity: float = Field(ge=0, description="可用数量")
    batch_no: str | None = Field(default=None, max_length=80, description="批次号")
    production_date: str | None = Field(default=None, description="生产日期 ISO 8601")
    expiry_date: str | None = Field(default=None, description="过期日期 ISO 8601")


class WmsInventoryQueryUnavailable(Exception):
    """WMS 库存查询暂时不可用，调用方可按 attempt 重试。"""


class WmsInventoryQueryContractError(Exception):
    """WMS provider 响应无法转换为稳定 Port 合同。"""


class WmsInventoryQueryPort(Protocol):
    """WMS 库存只读查询 port。

    query-only; 业务事务走 WmsInventoryTransactionPort。
    短 TTL 缓存 (主计划 §6: 30s) 避免高频轮询; cache_ttl_seconds 在
    ExternalContractProfile 中声明。
    """

    async def query_inventory(
        self,
        material_code: str,
        *,
        warehouse_code: str | None = None,
    ) -> list[WmsInventoryItem]:
        """查询物料库存 (按物料编码, 可选仓库过滤)。"""
        ...

    def query_empty_bins(
        self,
        warehouse_code: str,
        *,
        zone_code: str | None = None,
    ) -> list[str]:
        """查询仓库空库位列表 (按仓库编码, 可选 zone 过滤)。"""
        ...
