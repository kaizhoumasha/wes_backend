"""WMS 入站 callback 标准化。"""

from __future__ import annotations

from typing import Any

from src.app.callback.contracts.external_callbacks import (
    WMS_ALLOWED_CALLBACK_TYPES,
    WMS_ORDINARY_EVENT_TYPES,
    validate_external_callback_type,
)
from src.app.runtime.system_capabilities.wms.provider_catalog import (
    WMS_EFFECT_STATUS_HINT_CALLBACK,
    WMS_TYPED_EFFECT_CALLBACK_TYPES,
)

type JsonDict = dict[str, Any]


class WmsExecutionCallbackNormalizer:
    """只接受冻结 SPEC 的普通事件与 typed EFFECT status hint。"""

    def normalize(self, payload: JsonDict) -> JsonDict:
        callback_type = _require_first_str(payload, ("callback_type",), "callback_type")
        self.validate(payload, callback_type)
        trace_id = _require_first_str(payload, ("trace_id",), "trace_id")
        return {
            "callback_type": callback_type,
            "trace_id": trace_id,
            "payload": payload,
        }

    def validate(self, payload: JsonDict, callback_type: str) -> None:
        """校验 WMS callback 的冻结允许集；非 WMS provider 沿用通用入口合同。"""

        callback_type = validate_external_callback_type(payload, declared_callback_type=callback_type)
        if callback_type in WMS_TYPED_EFFECT_CALLBACK_TYPES:
            callback_data = payload.get("data")
            if not isinstance(callback_data, dict):
                raise ValueError("data must be an object")
            _ = WMS_EFFECT_STATUS_HINT_CALLBACK.payload_model.model_validate(callback_data)


def _require_first_str(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    value = _resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


def _resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


wms_execution_callback_normalizer = WmsExecutionCallbackNormalizer()


__all__ = [
    "WMS_ALLOWED_CALLBACK_TYPES",
    "WMS_ORDINARY_EVENT_TYPES",
    "WmsExecutionCallbackNormalizer",
    "wms_execution_callback_normalizer",
]
