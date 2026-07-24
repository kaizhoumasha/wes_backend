"""`full_box_exchange` typed request 到通用 DispatchEnvelope 的薄适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .gateway import FullBoxExchangeDispatchGateway

if TYPE_CHECKING:
    from src.app.sys.models import DispatchEnvelope
    from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest


class FullBoxExchangeEffectAdapter:
    """只保留 operation typed gateway；通用 Outbox 组装由 orchestration Service 负责。"""

    def __init__(self, *, gateway: FullBoxExchangeDispatchGateway | None = None) -> None:
        self._gateway = gateway or FullBoxExchangeDispatchGateway()

    def build_envelope(
        self,
        request: FullBoxExchangeOperationRequest,
        *,
        idempotency_key: str,
    ) -> DispatchEnvelope:
        return self._gateway.build_envelope(request, idempotency_key=idempotency_key)


full_box_exchange_effect_adapter = FullBoxExchangeEffectAdapter()

__all__ = ["FullBoxExchangeEffectAdapter", "full_box_exchange_effect_adapter"]
