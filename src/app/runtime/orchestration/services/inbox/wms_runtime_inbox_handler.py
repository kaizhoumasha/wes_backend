"""WMS 入站消息在 RuntimeInbox 消费阶段的窄适配器。"""

from __future__ import annotations

from typing import Any

from src.app.contracts.wms_inbound import WMS_BUSINESS_EVENT_TYPES


class WmsRuntimeInboxHandler:
    """消费已验真的普通 WMS event，并把 status hint 交给既有查询提示路由。"""

    def __init__(self, *, callback_router: Any | None = None) -> None:
        self._callback_router = callback_router

    def _resolve_callback_router(self) -> Any:
        if self._callback_router is None:
            from src.app.runtime.orchestration.services.inbox.wms_typed_effect_callback_router import (
                wms_typed_effect_callback_router,
            )

            self._callback_router = wms_typed_effect_callback_router
        return self._callback_router

    async def handle(
        self,
        db: Any,
        *,
        provider_code: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """返回是否消费；未知 provider/event 留给既有 RuntimeInbox 路径。"""

        if provider_code != "WMS":
            return False
        if event_type in WMS_BUSINESS_EVENT_TYPES:
            # callback admission 已按 event-specific typed contract 生成 canonical payload；
            # RuntimeInbox 只负责可靠消费，不能反向持有 inbound normalizer。
            return True
        if event_type != "WMS_EFFECT_STATUS_HINT":
            return False
        handled = await self._resolve_callback_router().route(
            db,
            callback_type=event_type,
            payload=payload,
        )
        if not handled:
            raise ValueError("WMS_EFFECT_STATUS_HINT_NOT_HANDLED")
        return True


__all__ = ["WmsRuntimeInboxHandler"]
