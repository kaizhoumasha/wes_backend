"""SMT 货架/料箱调度领域服务。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class SmtFullBoxExchangeRequest:
    """SMT 满箱交换外部请求。"""

    dispatch_key: str
    target_code: str
    payload: Mapping[str, Any]
    timeout_seconds: int = 1800
    source_system: str = "WMS_RCS"


@dataclass(frozen=True, slots=True)
class SmtRackBinSchedulingDecision:
    """SMT 货架/料箱调度决策。"""

    bin_location: Mapping[str, Any] | None = None
    full_box_exchange_request: SmtFullBoxExchangeRequest | None = None

    def __post_init__(self) -> None:
        if (self.bin_location is None) == (self.full_box_exchange_request is None):
            raise ValueError("SmtRackBinSchedulingDecision requires exactly one scheduling result")


class SmtRackBinSchedulingService:
    """负责 SMT 粗分机出料阶段的货架/料箱调度。

    v1 只提供确定性调度结果，先把原先散落在插件里的占位能力收敛到领域服务。
    后续接入真实 RackRelease / RackBinMount 时，应在此服务内替换调度策略。
    """

    BIN_TYPES = ("三格箱", "五格箱", "九格箱")

    def allocate(self, barcode: str) -> dict[str, Any]:
        """按物料业务键分配目标料箱位置。"""

        checksum = int(hashlib.md5(barcode.encode(), usedforsecurity=False).hexdigest()[:8], 16)
        return {
            "bin_id": f"BIN_{checksum % 900 + 100}",
            "bin_type": self.BIN_TYPES[checksum % len(self.BIN_TYPES)],
            "bin_cell_location": str(checksum % 9 + 1),
        }

    def plan_allocation(
        self,
        barcode: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> SmtRackBinSchedulingDecision:
        """生成出料阶段调度决策。"""

        _ = context
        return SmtRackBinSchedulingDecision(bin_location=self.allocate(barcode))


smt_rack_bin_scheduling_service = SmtRackBinSchedulingService()


__all__ = [
    "SmtFullBoxExchangeRequest",
    "SmtRackBinSchedulingDecision",
    "SmtRackBinSchedulingService",
    "smt_rack_bin_scheduling_service",
]
