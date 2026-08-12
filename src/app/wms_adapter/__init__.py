"""WMS 北向访问薄封装。"""

from typing import Any

from fastapi import APIRouter

from src.app.wms_adapter.client import WmsAccessResult, WmsClient
from src.app.wms_adapter.factory import build_wms_client
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
from src.app.wms_adapter.transport_event_handler import TransportEventHandler, TransportEventResponse

router_v1: APIRouter

__all__ = [
    "TransportEventHandler",
    "TransportEventResponse",
    "WmsAccessResult",
    "WmsClient",
    "WmsInboundAuthPolicy",
    "WmsTransportAdapter",
    "build_wms_client",
    "router_v1",
]


def __getattr__(name: str) -> Any:
    """按需构建 WMS Adapter router，避免基础客户端导入时装载 API 接线。"""

    if name != "router_v1":
        raise AttributeError(name)

    from src.app.wms_adapter.v1 import router as wms_router

    router_v1 = APIRouter(prefix="/v1/wms", tags=["WMS Transport"])
    router_v1.include_router(wms_router)
    globals()["router_v1"] = router_v1
    return router_v1
