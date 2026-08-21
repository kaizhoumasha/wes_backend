"""WMS 北向访问薄封装。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter

    from src.app.wms_adapter.client import WmsAccessResult, WmsClient
    from src.app.wms_adapter.factory import build_wms_client
    from src.app.wms_adapter.inbound_adapter import WmsInboundAdapter
    from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
    from src.app.wms_adapter.inbound_event_handler import InboundEventHandler, InboundEventResponse
    from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
    from src.app.wms_adapter.transport_event_handler import TransportEventHandler, TransportEventResponse

router_v1: APIRouter

_LAZY_EXPORTS = {
    "InboundEventHandler": ("src.app.wms_adapter.inbound_event_handler", "InboundEventHandler"),
    "InboundEventResponse": ("src.app.wms_adapter.inbound_event_handler", "InboundEventResponse"),
    "TransportEventHandler": ("src.app.wms_adapter.transport_event_handler", "TransportEventHandler"),
    "TransportEventResponse": ("src.app.wms_adapter.transport_event_handler", "TransportEventResponse"),
    "WmsAccessResult": ("src.app.wms_adapter.client", "WmsAccessResult"),
    "WmsClient": ("src.app.wms_adapter.client", "WmsClient"),
    "WmsInboundAdapter": ("src.app.wms_adapter.inbound_adapter", "WmsInboundAdapter"),
    "WmsInboundAuthPolicy": ("src.app.wms_adapter.inbound_auth", "WmsInboundAuthPolicy"),
    "WmsTransportAdapter": ("src.app.wms_adapter.transport_adapter", "WmsTransportAdapter"),
    "build_wms_client": ("src.app.wms_adapter.factory", "build_wms_client"),
}

__all__ = [
    "InboundEventHandler",
    "InboundEventResponse",
    "TransportEventHandler",
    "TransportEventResponse",
    "WmsAccessResult",
    "WmsClient",
    "WmsInboundAdapter",
    "WmsInboundAuthPolicy",
    "WmsTransportAdapter",
    "build_wms_client",
    "router_v1",
]


def __getattr__(name: str) -> Any:
    """按需装载 WMS Adapter，避免纯 wire 工具导入时装配运行时。"""

    if name == "router_v1":
        from fastapi import APIRouter

        from src.app.wms_adapter.v1 import router as wms_router

        router = APIRouter(prefix="/v1/wms", tags=["WMS Transport"])
        router.include_router(wms_router)
        globals()[name] = router
        return router

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)

    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
