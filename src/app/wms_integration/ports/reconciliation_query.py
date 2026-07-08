"""WmsReconciliationQueryPort。

主计划 §5.1 7 port 之一: 对账 drift 只读查询 (bin / rack / full 实体一致性)。
所有方法 query-only, 不写 WMS 业务, 与 §3.4 Authority Matrix "WES 维护
库存作业状态, WMS 维护库存" 一致; drift 由 WES reconciliation 任务消费,
本端口只提供查询入口。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_query
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsDriftItem(BaseModel):
    """WES-WMS 实体 drift 项。"""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(description="实体类型: BIN / RACK / FULL_BOX")
    entity_id: str = Field(min_length=1, max_length=80, description="实体 ID")
    wes_state: str = Field(description="WES 记录的实体状态")
    wms_state: str = Field(description="WMS 记录的实体状态")
    drift_kind: str = Field(description="MISSING_WES / MISSING_WMS / STATE_MISMATCH / QTY_MISMATCH")
    detected_at: str = Field(description="drift 检测时间 ISO 8601")


class WmsReconciliationQueryPort(Protocol):
    """WMS 对账查询 port。

    所有方法 query-only; drift 由 WES reconciliation 任务消费。
    Runtime capability 注入时仅暴露 query port contract (capability implementation import boundary)。
    """

    def check_bin_drift(self, warehouse_code: str, *, zone_code: str | None = None) -> list[WmsDriftItem]:
        """检查仓库 (可选 zone) 内 bin 实体 WES/WMS 一致性, 返回 drift 列表。"""
        ...

    def check_rack_drift(self, warehouse_code: str, *, station_code: str | None = None) -> list[WmsDriftItem]:
        """检查仓库 (可选工位) 内 rack 实体 WES/WMS 一致性, 返回 drift 列表。"""
        ...

    def check_full_drift(self, warehouse_code: str) -> list[WmsDriftItem]:
        """检查仓库内满箱实体 WES/WMS 一致性, 返回 drift 列表。"""
        ...
