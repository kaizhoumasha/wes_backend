"""WMS/RCS 执行回调标准化。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.system_capabilities.wms.provider_catalog import (
    WMS_EFFECT_STATUS_HINT_CALLBACK,
    WMS_TYPED_EFFECT_CALLBACK_TYPES,
)

type JsonDict = dict[str, Any]

WMS_RCS_EXECUTION_PREFIXES = ("WMS_", "RCS_")
WMS_RCS_SOURCE_SYSTEM_BY_PREFIX = {
    "WMS_": "WMS",
    "RCS_": "RCS",
}
WMS_RCS_EXECUTION_STATUS_ALIASES = ("task_status", "status", "result", "external_status", "exchange_status")
WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS = (
    "source_system",
    "source_event_id",
    "source_version",
    "occurred_at",
    "request_id",
    "timestamp",
    "signature",
)
WMS_RCS_FULL_BOX_EXCHANGE_CALLBACK_TYPES = frozenset(
    {
        "WMS_FULL_BOX_EXCHANGE_RESULT",
        "RCS_FULL_BOX_EXCHANGE_RESULT",
    }
)
WMS_RCS_FULL_BOX_EXCHANGE_REQUIRED_FIELDS = (
    "dispatch_key",
    "exchange_request_code",
    "rack_release_id",
    "wms_rcs_task_id",
    *WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS,
    "exchange_status",
)
WMS_RCS_RACK_CALLBACK_TYPES = frozenset(
    {
        "WMS_RACK_TASK_RESULT",
        "RCS_RACK_TASK_RESULT",
        "WMS_RACK_TASK_PROGRESS",
        "RCS_RACK_TASK_PROGRESS",
        "WMS_RACK_ARRIVED",
        "RCS_RACK_ARRIVED",
        "WMS_RACK_EXCHANGE_PROGRESS",
        "RCS_RACK_EXCHANGE_PROGRESS",
        "WMS_RACK_EXCHANGE_FAILED",
        "RCS_RACK_EXCHANGE_FAILED",
    }
)
WMS_RCS_RACK_STATUS_REQUIRED_CALLBACK_TYPES = frozenset(
    {
        "WMS_RACK_TASK_RESULT",
        "RCS_RACK_TASK_RESULT",
        "WMS_RACK_TASK_PROGRESS",
        "RCS_RACK_TASK_PROGRESS",
        "WMS_RACK_EXCHANGE_PROGRESS",
        "RCS_RACK_EXCHANGE_PROGRESS",
    }
)
WMS_RCS_RUNTIME_CAPABILITY_CALLBACK_TYPES = frozenset(
    {
        "WMS_ROUGH_SORTER_INBOUND",
    }
)


class WmsExecutionCallbackNormalizer:
    """校验并标准化 WMS/RCS execution callback 的最小包络。"""

    def normalize(self, payload: JsonDict) -> JsonDict:
        callback_type = _require_first_str(payload, ("callback_type",), "callback_type")
        self.validate(payload, callback_type)
        trace_id = _resolve_optional_str(payload, ("trace_id",))
        if trace_id is None and callback_type not in WMS_RCS_RACK_CALLBACK_TYPES:
            raise ValueError("trace_id is required")

        return {
            "callback_type": callback_type,
            "trace_id": trace_id,
            "payload": payload,
        }

    def validate(self, payload: JsonDict, callback_type: str) -> None:
        """校验 WMS/RCS 运行时执行回调第零阶段最小包络。"""

        if not callback_type.startswith(WMS_RCS_EXECUTION_PREFIXES):
            return

        if callback_type in WMS_RCS_RUNTIME_CAPABILITY_CALLBACK_TYPES:
            _require_payload_fields(payload, (*WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS, "runtime_capability"))
            _validate_wms_rcs_source_system(payload, callback_type)
            return

        if callback_type in WMS_TYPED_EFFECT_CALLBACK_TYPES:
            callback_data = _require_payload_value(payload, "data")
            if not isinstance(callback_data, dict):
                raise ValueError("data must be an object")
            _ = WMS_EFFECT_STATUS_HINT_CALLBACK.payload_model.model_validate(callback_data)
            _validate_wms_rcs_source_system(payload, callback_type)
            return

        _ = _require_payload_value(payload, "dispatch_key")
        if callback_type in WMS_RCS_FULL_BOX_EXCHANGE_CALLBACK_TYPES:
            _require_payload_fields(payload, WMS_RCS_FULL_BOX_EXCHANGE_REQUIRED_FIELDS)
            _validate_wms_rcs_source_system(payload, callback_type)
            return

        if callback_type in WMS_RCS_RACK_CALLBACK_TYPES:
            _require_payload_fields(payload, WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS)

            _validate_wms_rcs_source_system(payload, callback_type)

            if callback_type in WMS_RCS_RACK_STATUS_REQUIRED_CALLBACK_TYPES and not _resolve_first_str(
                payload, WMS_RCS_EXECUTION_STATUS_ALIASES
            ):
                raise ValueError("status is required")
            return

        if not _resolve_first_str(payload, WMS_RCS_EXECUTION_STATUS_ALIASES):
            raise ValueError("status is required")
        _validate_wms_rcs_source_system(payload, callback_type)


def _resolve_optional_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    value = _resolve_first_str(payload, aliases)
    return value or None


def _require_first_str(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    value = _resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


def _require_payload_value(payload: JsonDict, field_name: str) -> object:
    value = payload.get(field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


def _require_payload_fields(payload: JsonDict, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        _ = _require_payload_value(payload, field_name)


def _resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _validate_wms_rcs_source_system(payload: JsonDict, callback_type: str) -> None:
    source_system = _resolve_first_str(payload, ("source_system",))
    if source_system not in {"WMS", "RCS"}:
        raise ValueError("source_system must be WMS or RCS")
    _validate_callback_source_match(callback_type, source_system)


def _validate_callback_source_match(callback_type: str, source_system: str) -> None:
    expected_source = next(
        (
            expected_source
            for prefix, expected_source in WMS_RCS_SOURCE_SYSTEM_BY_PREFIX.items()
            if callback_type.startswith(prefix)
        ),
        None,
    )
    if expected_source is not None and source_system != expected_source:
        raise ValueError("source_system must match callback_type provider")


wms_execution_callback_normalizer = WmsExecutionCallbackNormalizer()


__all__ = [
    "WMS_RCS_FULL_BOX_EXCHANGE_CALLBACK_TYPES",
    "WMS_RCS_FULL_BOX_EXCHANGE_REQUIRED_FIELDS",
    "WMS_RCS_RACK_CALLBACK_TYPES",
    "WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS",
    "WMS_RCS_RUNTIME_CAPABILITY_CALLBACK_TYPES",
    "WmsExecutionCallbackNormalizer",
    "wms_execution_callback_normalizer",
]
