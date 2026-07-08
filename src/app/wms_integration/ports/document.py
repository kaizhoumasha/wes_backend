"""WmsDocumentPort。

主计划 §5.1 7 port 之一: 单据查询 (GRN / 拣货单 / 出库单 / 波次 / 任务快照)。
所有方法 query-only, 与 §3.4 Authority Matrix "WMS 是单据权威" 一致。
Runtime capability 注入时仅暴露 query port contract (capability implementation import boundary 禁止 internal
domain import wms_integration 实现)。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_query
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsGrnInfo(BaseModel):
    """GRN (Goods Receipt Note) 主单据。"""

    model_config = ConfigDict(extra="forbid")

    grn_id: str = Field(min_length=1, max_length=80, description="GRN 编号 (主键)")
    grn_type: str = Field(description="GRN 类型 (PO/SUB/RETURN)")
    status: str = Field(description="OPEN / IN_PROGRESS / COMPLETED / CLOSED")
    received_at: str = Field(description="收货时间 ISO 8601")
    total_items: int = Field(ge=0, description="GRN 明细总条数")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")


class WmsGrnItem(BaseModel):
    """GRN 单据明细行 (WMS 权威)。"""

    model_config = ConfigDict(extra="forbid")

    grn_id: str = Field(min_length=1, max_length=80, description="所属 GRN")
    material_code: str = Field(min_length=1, max_length=80, description="物料编码")
    quantity: float = Field(ge=0, description="收货数量")
    batch_no: str | None = Field(default=None, max_length=80, description="批次号")
    package_id: str | None = Field(default=None, description="已绑定料盘 ID")


class WmsPickOrder(BaseModel):
    """拣货单 (WMS 下发)。"""

    model_config = ConfigDict(extra="forbid")

    pick_order_id: str = Field(min_length=1, max_length=80, description="拣货单号")
    wave_id: str = Field(min_length=1, max_length=80, description="所属波次")
    status: str = Field(description="PENDING / DISPATCHED / COMPLETED")
    priority: int = Field(ge=0, le=10, description="优先级 0-10")
    total_lines: int = Field(ge=0, description="拣货行数")


class WmsOutboundOrder(BaseModel):
    """出库单 (WMS 下发)。"""

    model_config = ConfigDict(extra="forbid")

    outbound_order_id: str = Field(min_length=1, max_length=80, description="出库单号")
    customer_code: str = Field(min_length=1, max_length=80, description="客户编码")
    status: str = Field(description="PENDING / PICKED / SHIPPED")
    ship_date: str = Field(description="发货日期 ISO 8601")
    total_lines: int = Field(ge=0, description="出库行数")


class WmsWave(BaseModel):
    """波次 (WMS 下发, 包含多个拣货单)。"""

    model_config = ConfigDict(extra="forbid")

    wave_id: str = Field(min_length=1, max_length=80, description="波次号")
    status: str = Field(description="PLANNED / RELEASED / IN_PROGRESS / COMPLETED")
    scheduled_at: str = Field(description="计划开始时间 ISO 8601")
    pick_order_count: int = Field(ge=0, description="包含拣货单数")


class WmsTaskSnapshot(BaseModel):
    """任务快照 (WMS 权威状态)。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=80, description="任务 ID")
    task_type: str = Field(description="任务类型 (PKG / BIN / TRANSPORT)")
    status: str = Field(description="PENDING / ACTIVE / COMPLETED / FAILED")
    correlation_id: str = Field(description="与 WES RuntimeExecutionWorkItem 的关联 ID")
    updated_at: str = Field(description="最近更新时间 ISO 8601")


class WmsDocumentPort(Protocol):
    """WMS 单据 port。

    所有方法 query-only, 短 TTL 缓存 (主计划 §6: 60s); 业务事务/搬运不走本 port。
    Runtime capability 注入时仅暴露 query port contract (capability implementation import boundary 禁止内部域
    import wms_integration 实现)。
    """

    def get_grn(self, grn_id: str) -> WmsGrnInfo:
        """查询 GRN 主单据 (单条, 按 grn_id)。"""
        ...

    def list_grn_items(self, grn_id: str) -> list[WmsGrnItem]:
        """查询 GRN 单据明细行列表 (按 grn_id)。"""
        ...

    def get_pick_order(self, pick_order_id: str) -> WmsPickOrder:
        """查询拣货单 (单条, 按 pick_order_id)。"""
        ...

    def get_outbound_order(self, outbound_order_id: str) -> WmsOutboundOrder:
        """查询出库单 (单条, 按 outbound_order_id)。"""
        ...

    def get_wave(self, wave_id: str) -> WmsWave:
        """查询波次 (单条, 按 wave_id)。"""
        ...

    def get_task_snapshot(self, task_id: str) -> WmsTaskSnapshot:
        """查询任务快照 (单条, 按 task_id)。"""
        ...
