"""WmsFulfillmentPort。

主计划 §5.1 7 port 之一: 履约 (搬运/补给/换面/满箱交换)。
所有 effect 必先写 RuntimeIntentLog + EffectPort dispatcher (主计划 §3.5 I3 边界),
capability 不得在 WMS 履约上下文绕过 Runtime 直接修改 WES 内部状态。

方法只定义业务协议；运行准入由 typed system capability identity 承担。
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
