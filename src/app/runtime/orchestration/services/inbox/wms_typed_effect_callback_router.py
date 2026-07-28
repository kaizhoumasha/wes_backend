"""生产 WMS EFFECT callback 状态查询提示路由。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_EFFECT_STATUS_HINT_CALLBACK
from src.app.wms_integration.operation_registry import ASYNC_EFFECT_OPERATION_IDENTITIES


class WmsEffectStatusHintValidationError(ValueError):
    """通用 hint 包络或关联字段不符合冻结合同。"""


class WmsTypedEffectCallbackRouter:
    """识别通用 WMS EFFECT hint；callback 不再拥有业务终态写权限。"""

    def __init__(self, *, status_service: Any | None = None) -> None:
        self._status_service = status_service

    def _resolve_status_service(self) -> Any:
        if self._status_service is None:
            from src.app.runtime.orchestration.services.wms_effect_status_service import wms_effect_status_service

            self._status_service = wms_effect_status_service
        return self._status_service

    async def route(
        self,
        db: Any,
        *,
        callback_type: str,
        payload: dict[str, Any],
    ) -> bool:
        if callback_type != WMS_EFFECT_STATUS_HINT_CALLBACK.callback_type:
            return False
        callback_data = payload.get("data")
        if not isinstance(callback_data, dict):
            raise WmsEffectStatusHintValidationError("WMS_EFFECT_STATUS_HINT_SCHEMA_INVALID: data must be an object")

        try:
            hint = WMS_EFFECT_STATUS_HINT_CALLBACK.payload_model.model_validate(callback_data)
        except (TypeError, ValueError) as exc:
            raise WmsEffectStatusHintValidationError("WMS_EFFECT_STATUS_HINT_SCHEMA_INVALID") from exc
        if hint.operation_identity not in ASYNC_EFFECT_OPERATION_IDENTITIES:
            raise WmsEffectStatusHintValidationError("WMS_EFFECT_STATUS_HINT_OPERATION_UNKNOWN")

        _ = await self._resolve_status_service().request_status_check_hint(
            db,
            operation_identity=hint.operation_identity,
            idempotency_key=hint.idempotency_key,
            dispatch_key=hint.dispatch_key,
        )
        return True


wms_typed_effect_callback_router = WmsTypedEffectCallbackRouter()

__all__ = [
    "WmsEffectStatusHintValidationError",
    "WmsTypedEffectCallbackRouter",
    "wms_typed_effect_callback_router",
]
