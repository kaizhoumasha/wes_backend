"""`confirm_inbound` typed request 到通用 DispatchEnvelope 的薄适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .gateway import ConfirmInboundDispatchGateway

if TYPE_CHECKING:
    from src.app.sys.models import DispatchEnvelope
    from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest


class ConfirmInboundEffectAdapter:
    """只保留 operation typed gateway；通用 Outbox 组装由 orchestration Service 负责。"""

    def __init__(self, *, gateway: ConfirmInboundDispatchGateway | None = None) -> None:
        self._gateway = gateway or ConfirmInboundDispatchGateway()

    def build_envelope(
        self,
        request: ConfirmInboundOperationRequest,
        *,
        idempotency_key: str,
    ) -> DispatchEnvelope:
        return self._gateway.build_envelope(request, idempotency_key=idempotency_key)


confirm_inbound_effect_adapter = ConfirmInboundEffectAdapter()

__all__ = [
    "ConfirmInboundEffectAdapter",
    "confirm_inbound_effect_adapter",
]
