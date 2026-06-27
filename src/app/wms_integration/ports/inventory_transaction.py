"""WmsInventoryTransactionPort (Phase 1 CEO-001 #4)。

主计划 §5.1 7 port 之一: 库存事务 (reserve_inventory / release_reservation /
confirm_inbound / confirm_outbound / transfer_inventory)。
由现有 typed_ports.WmsInventoryPort 拆 transaction 部分。

所有 effect 必先写 RuntimeIntentLog + EffectPort (主计划 §3.5 I3 边界),
不得在业务 capability 直接修改 WES 内部状态。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsReservationResult(BaseModel):
    """reserve_inventory 返回结果。"""

    model_config = ConfigDict(extra="forbid")

    reservation_id: str = Field(min_length=1, max_length=80, description="WMS 预留单号")
    material_code: str = Field(description="物料编码")
    quantity: float = Field(ge=0, description="预留数量")
    warehouse_code: str = Field(description="仓库编码")


class WmsTransferResult(BaseModel):
    """transfer_inventory / confirm_inbound/outbound 返回结果。"""

    model_config = ConfigDict(extra="forbid")

    document_no: str = Field(min_length=1, max_length=80, description="WMS 单据号")
    material_code: str = Field(description="物料编码")
    quantity: float = Field(ge=0, description="操作数量")
    warehouse_code: str = Field(description="仓库编码")


class WmsInventoryTransactionPort(Protocol):
    """WMS 库存事务 port (Phase 1 CEO-001 #4)。

    所有 effect 经 RuntimeIntentLog + EffectPort dispatcher; capability 不得
    绕过 Runtime 直接修改 WES 内部状态 (主计划 §3.5 I3)。
    """

    def reserve_inventory(
        self,
        material_code: str,
        quantity: float,
        warehouse_code: str,
    ) -> WmsReservationResult:
        """预留库存 (短时占用, 后续 release 或 confirm)。"""
        ...

    def release_reservation(self, reservation_id: str) -> None:
        """释放库存预留 (不产生新单据)。"""
        ...

    def confirm_inbound(
        self,
        material_code: str,
        quantity: float,
        warehouse_code: str,
    ) -> WmsTransferResult:
        """确认入库 (产生 GRN 单据)。"""
        ...

    def confirm_outbound(
        self,
        material_code: str,
        quantity: float,
        warehouse_code: str,
    ) -> WmsTransferResult:
        """确认出库 (产生拣货/出库单据)。"""
        ...

    def transfer_inventory(
        self,
        material_code: str,
        quantity: float,
        from_warehouse: str,
        to_warehouse: str,
    ) -> WmsTransferResult:
        """仓库间调拨 (产生调拨单据)。"""
        ...
