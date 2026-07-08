"""WmsMasterDataPort。

主计划 §5.1 7 port 之一: 物料主数据 (material/area/warehouse/storage_location/equipment)。
所有方法 query-only, 不写 WMS 业务, 与 §3.4 Authority Matrix "WMS 是
库存主数据权威" 一致。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_query
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsMasterDataItem(BaseModel):
    """物料主数据项。"""

    model_config = ConfigDict(extra="forbid")

    material_code: str = Field(min_length=1, max_length=80, description="物料编码")
    material_name: str = Field(min_length=1, max_length=200, description="物料名称")
    unit: str = Field(description="计量单位")
    batch_managed: bool = Field(default=False, description="是否批次管理")
    serial_managed: bool = Field(default=False, description="是否序列号管理")


class WmsMasterDataPort(Protocol):
    """WMS 物料主数据 port。

    所有方法 query-only, 短 TTL 缓存 (主计划 §6: 30s)。
    Runtime capability 注入时仅暴露 query port contract (capability implementation import boundary 禁止内部域
    import wms_integration 实现)。
    """

    def get_material(self, material_code: str) -> WmsMasterDataItem:
        """查询物料主数据 (单条)。"""
        ...

    def list_materials(self, *, batch_managed: bool | None = None) -> list[WmsMasterDataItem]:
        """查询物料主数据列表 (支持 batch_managed 过滤)。"""
        ...
