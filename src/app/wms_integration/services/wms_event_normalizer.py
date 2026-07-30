"""WMS 入站事件 normalizer。

实现 WMS 回调入站事件 4 类公开顶层包络的 typed 转换。

设计边界:
- 纯函数转换: 不调用任何业务 capability / DB / 外部 HTTP
- callback API admission 是唯一 composition owner
- 本类无 persistent state，不注册 runtime singleton
"""

from __future__ import annotations

from typing import Any

from src.app.wms_integration.ports.event import (
    WmsGrnReceivedEvent,
    WmsInventoryUpdatedEvent,
    WmsPalletArrivedEvent,
    WmsPdaOperationRecordedEvent,
    WmsTypedBusinessEvent,
)

_DISPATCH_TABLE: dict[str, str] = {
    "WMS_GRN_RECEIVED": "normalize_wms_grn_received",
    "WMS_PALLET_ARRIVED": "normalize_wms_pallet_arrived",
    "WMS_INVENTORY_UPDATED": "normalize_wms_inventory_updated",
    "WMS_PDA_OPERATION_RECORDED": "normalize_wms_pda_operation_recorded",
}


class WmsEventNormalizer:
    """WMS 回调入站 normalizer。"""

    def normalize_wms_grn_received(self, raw_payload: dict) -> WmsGrnReceivedEvent:
        """标准化 WMS_GRN_RECEIVED 回调。"""
        return WmsGrnReceivedEvent(**raw_payload)

    def normalize_wms_pallet_arrived(self, raw_payload: dict) -> WmsPalletArrivedEvent:
        """标准化 WMS_PALLET_ARRIVED 回调。"""
        return WmsPalletArrivedEvent(**raw_payload)

    def normalize_wms_inventory_updated(self, raw_payload: dict) -> WmsInventoryUpdatedEvent:
        """标准化 WMS_INVENTORY_UPDATED 回调。"""
        return WmsInventoryUpdatedEvent(**raw_payload)

    def normalize_wms_pda_operation_recorded(self, raw_payload: dict) -> WmsPdaOperationRecordedEvent:
        """标准化 WMS_PDA_OPERATION_RECORDED 回调。"""
        return WmsPdaOperationRecordedEvent(**raw_payload)

    def dispatch(self, event_type: str, raw_payload: dict[str, Any]) -> WmsTypedBusinessEvent:
        """按 event_type 派发到对应 normalize_wms_* 方法, 未知 event_type 抛 ValueError。"""
        method_name = _DISPATCH_TABLE.get(event_type)
        if method_name is None:
            raise ValueError(f"unknown wms event_type: {event_type!r}")
        method = getattr(self, method_name)
        return method(raw_payload)


__all__ = ["WmsEventNormalizer"]
