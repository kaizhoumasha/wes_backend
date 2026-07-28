"""WmsDocumentPort — @deferred to 全量联调。

本 Port 定义 WMS 单据查询能力合同。当前里程碑的粗分机/分拣机流程
通过 WmsMasterDataPort + InventoryQueryOperationPort 满足物料校验需求，
不需要独立的单据查询 Port。

激活条件: WMS 全量集成或业务需求明确需要 GRN/工单查询。

主计划 §5.1 7 port 之一: 单据查询 (GRN / 拣货单 / 出库单 / 波次 / 任务快照)。
所有方法 query-only, 与 §3.4 Authority Matrix "WMS 是单据权威" 一致。
Runtime capability 注入时仅暴露 query port contract。
capability implementation import boundary 禁止 internal domain import wms_integration 实现。

方法只定义业务协议；运行准入由 typed system capability identity 承担。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsGrnInfo(BaseModel):
    """GRN 是 WMS 从 SAP 获取的单条 PO 行到货记录。"""

    model_config = ConfigDict(extra="forbid")

    grn_id: str = Field(min_length=1, max_length=120, description="GRN 编号")
    po_number: str = Field(min_length=1, max_length=120, description="采购订单号")
    po_item: str = Field(min_length=1, max_length=120, description="采购订单行")
    material_code: str = Field(min_length=1, max_length=80, description="物料编码")
    planned_quantity: float = Field(ge=0, description="计划到货数量")
    received_quantity: float = Field(ge=0, description="已收数量")
    remaining_quantity: float = Field(ge=0, description="剩余数量")
    batch_no: str | None = Field(default=None, max_length=80, description="批次号")
    quality_status: str = Field(description="WMS 质检状态")


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
    Runtime capability 注入时仅暴露 query port contract。
    capability implementation import boundary 禁止内部域 import wms_integration 实现。
    """

    def get_grn(self, grn_id: str) -> WmsGrnInfo:
        """查询 GRN 主单据 (单条, 按 grn_id)。"""
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
