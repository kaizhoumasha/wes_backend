"""WMS 北向访问薄封装。"""

from src.app.wms_adapter.client import WmsAccessResult, WmsClient
from src.app.wms_adapter.factory import build_wms_client

__all__ = ["WmsAccessResult", "WmsClient", "build_wms_client"]
