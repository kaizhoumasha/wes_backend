"""生产 WMS EFFECT callback 状态查询提示路由。"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_EFFECT_STATUS_HINT_CALLBACK
from src.app.wms_integration.operation_registry import ASYNC_EFFECT_OPERATION_IDENTITIES


class WmsEffectStatusHintValidationError(ValueError):
    """通用 hint 包络或关联字段不符合冻结合同。"""


def _emit_callback_hint_observation(
    outcome: str,
    *,
    operation_identity: object = None,
    dispatch_key: object = None,
) -> None:
    """Callback 观测不参与校验、持久化或 ACK 判定。"""

    from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

    with suppress(Exception):
        _ = emit_wms_effect_observation(
            "wms_effect.callback_hint",
            operation_identity=operation_identity if isinstance(operation_identity, str) else None,
            dispatch_key=dispatch_key if isinstance(dispatch_key, str) else None,
            attributes={"outcome": outcome},
        )


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
            _emit_callback_hint_observation("REJECTED")
            raise WmsEffectStatusHintValidationError("WMS_EFFECT_STATUS_HINT_SCHEMA_INVALID: data must be an object")

        try:
            hint = WMS_EFFECT_STATUS_HINT_CALLBACK.payload_model.model_validate(callback_data)
        except (TypeError, ValueError) as exc:
            _emit_callback_hint_observation(
                "REJECTED",
                operation_identity=callback_data.get("operation_identity"),
                dispatch_key=callback_data.get("dispatch_key"),
            )
            raise WmsEffectStatusHintValidationError("WMS_EFFECT_STATUS_HINT_SCHEMA_INVALID") from exc
        if hint.operation_identity not in ASYNC_EFFECT_OPERATION_IDENTITIES:
            _emit_callback_hint_observation(
                "REJECTED",
                operation_identity=hint.operation_identity,
                dispatch_key=hint.dispatch_key,
            )
            raise WmsEffectStatusHintValidationError("WMS_EFFECT_STATUS_HINT_OPERATION_UNKNOWN")

        _emit_callback_hint_observation(
            "RECEIVED",
            operation_identity=hint.operation_identity,
            dispatch_key=hint.dispatch_key,
        )
        try:
            result = await self._resolve_status_service().request_status_check_hint(
                db,
                operation_identity=hint.operation_identity,
                idempotency_key=hint.idempotency_key,
                dispatch_key=hint.dispatch_key,
            )
        except ValueError:
            _emit_callback_hint_observation(
                "REJECTED",
                operation_identity=hint.operation_identity,
                dispatch_key=hint.dispatch_key,
            )
            raise
        _emit_callback_hint_observation(
            "QUERY_TRIGGERED" if result.outcome == "SCHEDULED" else "DUPLICATE",
            operation_identity=hint.operation_identity,
            dispatch_key=hint.dispatch_key,
        )
        return True


wms_typed_effect_callback_router = WmsTypedEffectCallbackRouter()

__all__ = [
    "WmsEffectStatusHintValidationError",
    "WmsTypedEffectCallbackRouter",
    "wms_typed_effect_callback_router",
]
