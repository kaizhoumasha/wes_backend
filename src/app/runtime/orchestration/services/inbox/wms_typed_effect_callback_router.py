"""生产 WMS typed EFFECT callback 编排路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import (
    CALLBACK_CONTRACT as FULL_BOX_EXCHANGE_CALLBACK,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import (
    CALLBACK_CONTRACT as NOTIFY_PACKAGE_BINDING_CALLBACK,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.contract import (
    CALLBACK_CONTRACT as CONFIRM_INBOUND_CALLBACK,
)
from src.app.sys.models.outbox import SystemOutboxStatus
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository

if TYPE_CHECKING:
    from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge


@dataclass(frozen=True, slots=True)
class _CallbackRoute:
    contract: Any
    identity_fields: tuple[str, ...]


_CALLBACK_ROUTES = {
    CONFIRM_INBOUND_CALLBACK.callback_type: _CallbackRoute(CONFIRM_INBOUND_CALLBACK, ("inbound_key",)),
    NOTIFY_PACKAGE_BINDING_CALLBACK.callback_type: _CallbackRoute(
        NOTIFY_PACKAGE_BINDING_CALLBACK,
        ("package_id", "pallet_id"),
    ),
    FULL_BOX_EXCHANGE_CALLBACK.callback_type: _CallbackRoute(
        FULL_BOX_EXCHANGE_CALLBACK,
        ("rack_id", "empty_box_id", "full_box_id"),
    ),
}


class WmsTypedEffectCallbackRouter:
    """识别 typed EFFECT callback；非 typed callback 保持现有生命周期路由。"""

    def __init__(
        self,
        *,
        outbox_repository: SystemOutboxRepository | None = None,
        reconciliation_bridge: EffectReconciliationBridge | None = None,
    ) -> None:
        self._outbox_repository = outbox_repository or SystemOutboxRepository()
        self._reconciliation_bridge = reconciliation_bridge
        self._callback_adapters: dict[str, Any] = {}

    def _resolve_reconciliation_bridge(self) -> EffectReconciliationBridge:
        if self._reconciliation_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_reconciliation_bridge

            self._reconciliation_bridge = effect_reconciliation_bridge
        return self._reconciliation_bridge

    def _resolve_callback_adapter(self, callback_type: str) -> Any:
        adapter = self._callback_adapters.get(callback_type)
        if adapter is not None:
            return adapter
        from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.callback_adapter import (
            full_box_exchange_callback_adapter,
        )
        from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.callback_adapter import (
            notify_package_binding_callback_adapter,
        )
        from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.callback_adapter import (
            confirm_inbound_callback_adapter,
        )

        self._callback_adapters.update(
            {
                CONFIRM_INBOUND_CALLBACK.callback_type: confirm_inbound_callback_adapter,
                NOTIFY_PACKAGE_BINDING_CALLBACK.callback_type: notify_package_binding_callback_adapter,
                FULL_BOX_EXCHANGE_CALLBACK.callback_type: full_box_exchange_callback_adapter,
            }
        )
        return self._callback_adapters[callback_type]

    async def route(
        self,
        db: Any,
        *,
        callback_type: str,
        payload: dict[str, Any],
        occurred_at_ms: int,
        source_event_id: str,
    ) -> bool:
        route = _CALLBACK_ROUTES.get(callback_type)
        if route is None:
            return False
        callback_data = payload.get("data")
        if not isinstance(callback_data, dict):
            raise TypeError(f"{callback_type} data must be an object")
        result = route.contract.payload_model.model_validate(callback_data)
        outbox = await self._outbox_repository.get_by_dispatch_key_for_update(db, result.dispatch_key)
        if outbox is None:
            raise ValueError(f"{callback_type} dispatch_key does not reference a frozen outbox")

        frozen_payload = getattr(outbox, "payload_json", None)
        if not isinstance(frozen_payload, dict):
            raise TypeError(f"{callback_type} frozen outbox payload is invalid")
        operation_matches = getattr(outbox, "operation_identity", None) == route.contract.operation.identity
        identity_matches = all(frozen_payload.get(field) == getattr(result, field) for field in route.identity_fields)
        if not operation_matches or not identity_matches:
            await self._open_and_isolate(
                db,
                dispatch_key=result.dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                reason_code="WMS_CALLBACK_BUSINESS_IDENTITY_MISMATCH",
                evidence_json={
                    "callback_type": callback_type,
                    "operation_identity": getattr(outbox, "operation_identity", None),
                    "callback_identity": {field: getattr(result, field) for field in route.identity_fields},
                    "frozen_identity": {field: frozen_payload.get(field) for field in route.identity_fields},
                },
            )
            return True

        if getattr(outbox, "status", None) not in {
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.SENT,
            SystemOutboxStatus.UNKNOWN,
            SystemOutboxStatus.FAILED,
        }:
            await self._open_and_isolate(
                db,
                dispatch_key=result.dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=source_event_id,
                reason_code="WMS_CALLBACK_BEFORE_DISPATCH",
                evidence_json={
                    "callback_type": callback_type,
                    "outbox_status": str(getattr(outbox, "status", None)),
                },
            )
            return True

        await self._resolve_callback_adapter(callback_type).record(
            db,
            result=result,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
        )
        _ = await self._outbox_repository.finish_sent_external_by_dispatch_key(db, result.dispatch_key)
        return True

    async def _open_and_isolate(
        self,
        db: Any,
        *,
        dispatch_key: str,
        occurred_at_ms: int,
        source_event_id: str,
        reason_code: str,
        evidence_json: dict[str, Any],
    ) -> None:
        """原子打开对账 case 并终止对应 outbox 的继续派发。"""

        await self._resolve_reconciliation_bridge().open(
            db,
            dispatch_key=dispatch_key,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            reason_code=reason_code,
            evidence_json=evidence_json,
        )
        isolated = await self._outbox_repository.isolate_for_reconciliation_by_dispatch_key(
            db,
            dispatch_key,
            reason=reason_code,
        )
        if isolated is None:
            raise RuntimeError(f"reconciliation outbox disappeared: dispatch_key={dispatch_key}")


wms_typed_effect_callback_router = WmsTypedEffectCallbackRouter()

__all__ = ["WmsTypedEffectCallbackRouter", "wms_typed_effect_callback_router"]
