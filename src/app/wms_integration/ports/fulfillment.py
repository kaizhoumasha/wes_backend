"""WmsFulfillmentPort。

主计划 §5.1 7 port 之一: 履约 (搬运/补给/换面/满箱交换/notify pkg binding)。
所有 effect 必先写 RuntimeIntentLog + EffectPort dispatcher (主计划 §3.5 I3 边界),
capability 不得在 WMS 履约上下文绕过 Runtime 直接修改 WES 内部状态。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_effect
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsFulfillmentResult(BaseModel):
    """WMS 履约请求结果。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80, description="WMS 履约请求号")
    accepted: bool = Field(description="WMS 是否接受请求")
    reason: str | None = Field(default=None, description="拒绝原因 (accepted=False 时必填)")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")


class WmsPalletBindingResult(BaseModel):
    """料盘绑定结果 (notify_pkg_binding 返回)。"""

    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(min_length=1, max_length=80, description="料盘 ID")
    pallet_id: str = Field(min_length=1, max_length=80, description="托盘 ID")
    bound_at: str = Field(description="绑定时间 ISO 8601")
    station_code: str = Field(min_length=1, max_length=80, description="绑定工位编码")


class WmsFulfillmentPort(Protocol):
    """WMS 履约 port。

    7 个 effect 方法覆盖 WES → WMS 的出站履约调用。所有 effect 经
    RuntimeIntentLog + EffectPort dispatcher; capability 不得绕过 Runtime
    直接修改 WES 内部状态 (主计划 §3.5 I3)。
    """

    def request_rack_supply(self, rack_id: str, material_code: str, quantity: float) -> WmsFulfillmentResult:
        """请求 WMS 给指定货架补给物料。"""
        ...

    def request_rack_transport(self, rack_id: str, from_station: str, to_station: str) -> WmsFulfillmentResult:
        """请求 WMS 搬运货架 (从 from_station 到 to_station)。"""
        ...

    def change_rack_face(self, rack_id: str, face: str) -> WmsFulfillmentResult:
        """请求 WMS 切换货架面 (face=A/B)。"""
        ...

    def full_box_exchange(self, rack_id: str, empty_box_id: str, full_box_id: str) -> WmsFulfillmentResult:
        """请求 WMS 满箱/空箱交换。"""
        ...

    def move_bin_to_conveyor_entry(self, bin_id: str, conveyor_entry: str) -> WmsFulfillmentResult:
        """请求 WMS 把料箱移到传送带入口。"""
        ...

    def move_bin_to_conveyor_exit(self, bin_id: str, conveyor_exit: str) -> WmsFulfillmentResult:
        """请求 WMS 把料箱移到传送带出口。"""
        ...

    def notify_pkg_binding(self, package_id: str, pallet_id: str, station_code: str) -> WmsPalletBindingResult:
        """通知 WMS 料盘已绑定托盘 (返回 binding 结果)。"""
        ...
