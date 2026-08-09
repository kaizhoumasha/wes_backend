"""WMS 北向访问薄封装。"""

from src.app.wms_adapter.client import WmsAccessResult, WmsClient
from src.app.wms_adapter.factory import build_wms_client
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
from src.app.wms_adapter.transport_event_handler import TransportEventHandler, TransportEventResponse

__all__ = [
    "TransportEventHandler",
    "TransportEventResponse",
    "WmsAccessResult",
    "WmsClient",
    "WmsTransportAdapter",
    "build_wms_client",
]
