"""Inbox 输入标准化。"""

from __future__ import annotations

from typing import Any

from src.workline_plugin_registry import classify_workline_result, resolve_workline_business_key
from src.workline_runtime.contracts import SixInOne
from src.workline_runtime.plugin_sdk.classifiers.result_classifier import (
    classify_result,
    classify_result_category,
    normalize_result_classification,
)
from src.workline_runtime.plugin_sdk.contracts import (
    NormalizedCommandResult,
    NormalizedDeviceEvent,
    NormalizedExternalCallback,
)
from src.workline_runtime.utils import non_empty_str, payload_dict

_ERROR_CODE_FIELDS = ("error_code", "code")
_ERROR_MESSAGE_FIELDS = ("error_message", "msg", "message")


def _resolve_first_str(payload: dict[str, Any], field_names: tuple[str, ...]) -> str | None:
    for field_name in field_names:
        value = non_empty_str(payload.get(field_name))
        if value:
            return value
    return None


def _infer_kind(raw_kind: Any, payload: dict[str, Any]) -> Any:
    """在 kind 缺失时，按 payload 形状做最小推断。

    注意：只有在 inbox.kind 缺失/为空时才推断，
    显式 DEVICE_EVENT 不允许被 payload 中的 command_code 等字段覆盖。
    """

    kind = getattr(raw_kind, "value", raw_kind)
    if isinstance(kind, str) and kind:
        return kind

    if payload.get("result") is not None and (
        payload.get("command_code") or payload.get("command_type") or payload.get("task_type")
    ):
        return "COMMAND_RESULT"

    if payload.get("callback_type") or payload.get("source_system"):
        return "EXTERNAL_HTTP"

    return kind


def _normalized_error_detail(payload: dict[str, Any]) -> dict[str, Any]:
    error_detail = payload_dict(payload.get("error_detail"))
    if error_detail:
        normalized_error_detail = dict(error_detail)
        resolved_error_code = _resolve_first_str(error_detail, _ERROR_CODE_FIELDS)
        resolved_error_message = _resolve_first_str(error_detail, _ERROR_MESSAGE_FIELDS)
        normalized_error_detail.setdefault(
            "error_code",
            resolved_error_code,
        )
        normalized_error_detail.setdefault(
            "error_message",
            resolved_error_message,
        )
        return normalized_error_detail

    return {}


def _resolve_trace_id(inbox: Any, payload: dict[str, Any], *, trace_id: str = "") -> str | None:
    return (
        non_empty_str(trace_id)
        or non_empty_str(payload.get("trace_id"))
        or non_empty_str(getattr(inbox, "trace_id", None))
    )


def _resolve_device_event_business_key(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    plugin_key: str | None = None,
) -> str | None:
    try:
        plugin_business_key = resolve_workline_business_key(plugin_key, payload)
    except (TypeError, ValueError):
        plugin_business_key = None
    if plugin_business_key:
        return plugin_business_key

    canonical_six_in_one = SixInOne.model_validate(data)
    if canonical_six_in_one.business_key:
        return canonical_six_in_one.business_key

    return (
        non_empty_str(payload.get("business_key"))
        or non_empty_str(data.get("business_key"))
        or non_empty_str(data.get("barcode"))
    )


def normalize_inbox_input(inbox: Any, *, trace_id: str = "", plugin_key: str | None = None) -> Any:
    """按 inbox 类型构建标准化输入模型。"""

    payload = payload_dict(getattr(inbox, "payload_json", None))
    kind = _infer_kind(getattr(inbox, "kind", None), payload)

    if kind == "COMMAND_RESULT":
        source_result = str(payload.get("result") or "UNKNOWN")
        error_detail = _normalized_error_detail(payload)
        plugin_classification = classify_workline_result(plugin_key, payload)
        result_classification = normalize_result_classification(plugin_classification) or classify_result_category(
            source_result,
            error_detail=error_detail,
        )
        return NormalizedCommandResult(
            command_code=str(payload.get("command_code") or ""),
            source_result=source_result,
            normalized_result=classify_result(source_result),
            result_classification=result_classification,
            command_type=non_empty_str(payload.get("command_type")) or non_empty_str(payload.get("task_type")),
            device_code=non_empty_str(payload.get("device_code")),
            trace_id=_resolve_trace_id(inbox, payload, trace_id=trace_id),
            finish_time=payload.get("finish_time"),
            payload=payload,
            data=payload_dict(payload.get("data")),
            error_detail=error_detail,
        )

    if kind == "EXTERNAL_HTTP":
        return NormalizedExternalCallback(
            callback_type=str(payload.get("callback_type") or payload.get("message_type") or "EXTERNAL_HTTP"),
            trace_id=_resolve_trace_id(inbox, payload, trace_id=trace_id),
            source_system=non_empty_str(payload.get("source_system")),
            payload=payload,
            attributes=payload_dict(payload.get("attributes")),
        )

    source_event_type = str(payload.get("event_type") or payload.get("message_type") or "UNKNOWN")
    data = payload_dict(payload.get("data"))
    canonical_event_type = str(payload.get("canonical_event_type") or source_event_type)
    return NormalizedDeviceEvent(
        source_event_type=source_event_type,
        canonical_event_type=canonical_event_type,
        device_code=non_empty_str(payload.get("device_code")),
        business_key=_resolve_device_event_business_key(payload, data, plugin_key=plugin_key),
        trace_id=_resolve_trace_id(inbox, payload, trace_id=trace_id),
        event_time=payload.get("timestamp"),
        payload=payload,
        data=data,
        attributes=payload_dict(payload.get("attributes")),
    )


__all__ = ["normalize_inbox_input"]
