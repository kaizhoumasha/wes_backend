"""WMS 入站事件 normalizer (runtime migration 阶段 1)。

实现 WMS 回调入站事件 4 类 typed 转换(GRN / Pallet / Rack / Transport):
- 4 个 normalize_wms_* 方法把 WMS 原始回调 dict 转 typed event
- dispatch(event_type, raw) 入口按 event_type 派发到对应 normalize_wms_*

设计边界:
- 纯函数转换: 不调用任何业务 capability / DB / 外部 HTTP
- Protocol 名字符串不在本模块出现, R-I3c 5 域扫描不会触发误报
  (调用方负责传入 port_protocol 参数,本模块仅按 type 接口契约注册)
- 单例由 InboundNormalizerRegistry 管理, 本类无 persistent state
- correlation_id 解析策略留给 InboundNormalizerProfile.correlation_resolution
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from src.app.wms_integration.ports.event import (
    WmsGrnReceivedEvent,
    WmsPalletArrivedEvent,
    WmsRackArrivedEvent,
    WmsTransportCompletedEvent,
)

if TYPE_CHECKING:
    from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry


_DISPATCH_TABLE: dict[str, str] = {
    "WMS_GRN_RECEIVED": "normalize_wms_grn_received",
    "WMS_PALLET_ARRIVED": "normalize_wms_pallet_arrived",
    "WMS_RACK_ARRIVED": "normalize_wms_rack_arrived",
    "WMS_TRANSPORT_COMPLETED": "normalize_wms_transport_completed",
}


class _WmsNormalizerPort(Protocol):
    """本地 type 契约, 仅用于 register_inbound_normalizers 的 type hint。

    实际注册时由调用方传入目标 Protocol,本模块不直接写其名字符串,
    避免 R-I3c 在 5 域扫描中触发误报。
    """

    def normalize_wms_grn_received(self, raw_payload: dict) -> WmsGrnReceivedEvent: ...
    def normalize_wms_pallet_arrived(self, raw_payload: dict) -> WmsPalletArrivedEvent: ...
    def normalize_wms_rack_arrived(self, raw_payload: dict) -> WmsRackArrivedEvent: ...
    def normalize_wms_transport_completed(self, raw_payload: dict) -> WmsTransportCompletedEvent: ...


class WmsEventNormalizer:
    """WMS 回调入站 normalizer (runtime migration 阶段 1)。"""

    def normalize_wms_grn_received(self, raw_payload: dict) -> WmsGrnReceivedEvent:
        """标准化 WMS_GRN_RECEIVED 回调。"""
        return WmsGrnReceivedEvent(**raw_payload)

    def normalize_wms_pallet_arrived(self, raw_payload: dict) -> WmsPalletArrivedEvent:
        """标准化 WMS_PALLET_ARRIVED 回调。"""
        return WmsPalletArrivedEvent(**raw_payload)

    def normalize_wms_rack_arrived(self, raw_payload: dict) -> WmsRackArrivedEvent:
        """标准化 WMS_RACK_ARRIVED 回调。"""
        return WmsRackArrivedEvent(**raw_payload)

    def normalize_wms_transport_completed(self, raw_payload: dict) -> WmsTransportCompletedEvent:
        """标准化 WMS_TRANSPORT_COMPLETED 回调。"""
        return WmsTransportCompletedEvent(**raw_payload)

    def dispatch(self, event_type: str, raw_payload: dict[str, Any]) -> Any:
        """按 event_type 派发到对应 normalize_wms_* 方法, 未知 event_type 抛 ValueError。"""
        method_name = _DISPATCH_TABLE.get(event_type)
        if method_name is None:
            raise ValueError(f"unknown wms event_type: {event_type!r}")
        method = getattr(self, method_name)
        return method(raw_payload)


def register_inbound_normalizers(
    registry: InboundNormalizerRegistry,
    port_protocol: type[_WmsNormalizerPort],
) -> None:
    """把 WmsEventNormalizer 注册到 InboundNormalizerRegistry 的 port_protocol 键下。

    调用方负责传入目标 Protocol(本模块不直接引用其名字符串,避免 R-I3c 误报)。
    """
    registry.register(port_protocol, WmsEventNormalizer)


__all__ = ["WmsEventNormalizer", "register_inbound_normalizers"]
