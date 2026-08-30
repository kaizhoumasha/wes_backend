"""WMS 入站认证的启动时冻结策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_TYPED_WMS_EVENT_TYPES = frozenset(
    {
        "WMS_GRN_RECEIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PALLET_ARRIVED",
        "WMS_PDA_OPERATION_RECORDED",
    }
)


@dataclass(frozen=True, slots=True)
class WmsInboundAuthPolicy:
    """隔离局域网内固定使用 NONE，并仅准入已知 typed WMS event。"""

    @property
    def allows_unsigned_wms_callbacks(self) -> bool:
        return True

    def permits_unsigned_event(self, payload: dict[str, Any]) -> bool:
        source_system = payload.get("source_system")
        event_type = payload.get("event_type")
        return (
            self.allows_unsigned_wms_callbacks
            and isinstance(source_system, str)
            and source_system == "WMS"
            and isinstance(event_type, str)
            and event_type in _TYPED_WMS_EVENT_TYPES
        )


__all__ = ["WmsInboundAuthPolicy"]
