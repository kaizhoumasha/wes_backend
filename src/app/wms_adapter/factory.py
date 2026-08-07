"""WMS 北向访问客户端构造入口。"""

from __future__ import annotations

from src.app.wms_adapter.client import WmsClient
from src.core.outbound_http import build_outbound_http_transport


def build_wms_client(*, base_url: str, timeout_seconds: float) -> WmsClient:
    """构造供单一运行时长期持有的 WMS Client。"""

    return WmsClient(
        build_outbound_http_transport(
            system_id="wms",
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    )


__all__ = ["build_wms_client"]
