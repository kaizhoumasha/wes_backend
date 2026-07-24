"""料盘绑定 typed callback 到 T8d reducer 的唯一适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.effect_bridges import (
    EffectCallbackBridge,
    EffectCallbackOutcome,
    effect_callback_bridge,
)

if TYPE_CHECKING:
    from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationResult


class NotifyPackageBindingCallbackAdapter:
    """只翻译 typed 业务结果；不直接写 RuntimeIntentLog 或 reconciliation case。"""

    def __init__(self, *, bridge: EffectCallbackBridge = effect_callback_bridge) -> None:
        self._bridge = bridge

    async def record(
        self,
        db: Any,
        *,
        result: NotifyPackageBindingOperationResult,
        occurred_at_ms: int,
        source_event_id: str,
    ) -> Any:
        outcome = EffectCallbackOutcome.COMPLETED if result.accepted else EffectCallbackOutcome.REJECTED
        return await self._bridge.record(
            db,
            dispatch_key=result.dispatch_key,
            outcome=outcome,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            reason_code=result.reason_code,
            evidence_json=result.model_dump(mode="json"),
        )


notify_package_binding_callback_adapter = NotifyPackageBindingCallbackAdapter()

__all__ = [
    "NotifyPackageBindingCallbackAdapter",
    "notify_package_binding_callback_adapter",
]
