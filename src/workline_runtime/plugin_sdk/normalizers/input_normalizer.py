"""Inbox 输入标准化。"""

from __future__ import annotations

from typing import Any

from src.workline_runtime.plugin_sdk.classifiers.result_classifier import classify_result
from src.workline_runtime.plugin_sdk.contracts import (
    NormalizedCommandResult,
    NormalizedDeviceEvent,
    NormalizedExternalCallback,
)


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
    error_detail = _payload_dict(payload.get("error_detail"))
    if error_detail:
        return error_detail

    fallback = {
        "error_code": payload.get("error_code"),
        "error_message": payload.get("error_message"),
    }
    return {key: value for key, value in fallback.items() if value is not None}


def _resolve_correlation_id(inbox: Any, payload: dict[str, Any], *, correlation_id: str = "") -> str | None:
    return (
        _non_empty_str(correlation_id)
        or _non_empty_str(payload.get("correlation_id"))
        or _non_empty_str(getattr(inbox, "correlation_id", None))
    )


def _resolve_device_event_business_key(payload: dict[str, Any], data: dict[str, Any]) -> str | None:
    return (
        _non_empty_str(payload.get("business_key"))
        or _non_empty_str(data.get("business_key"))
        or _non_empty_str(data.get("barcode"))
    )


def normalize_inbox_input(inbox: Any, *, correlation_id: str = "") -> Any:
    """按 inbox 类型构建标准化输入模型。"""

    payload = _payload_dict(getattr(inbox, "payload_json", None))
    kind = _infer_kind(getattr(inbox, "kind", None), payload)

    if kind == "COMMAND_RESULT":
        source_result = str(payload.get("result") or "UNKNOWN")
        return NormalizedCommandResult(
            command_code=str(payload.get("command_code") or ""),
            source_result=source_result,
            normalized_result=classify_result(source_result),
            command_type=_non_empty_str(payload.get("command_type")) or _non_empty_str(payload.get("task_type")),
            device_code=_non_empty_str(payload.get("device_code")),
            correlation_id=_resolve_correlation_id(inbox, payload, correlation_id=correlation_id),
            finish_time=payload.get("finish_time"),
            payload=payload,
            data=_payload_dict(payload.get("data")),
            error_detail=_normalized_error_detail(payload),
        )

    if kind == "EXTERNAL_HTTP":
        return NormalizedExternalCallback(
            callback_type=str(payload.get("callback_type") or payload.get("message_type") or "EXTERNAL_HTTP"),
            correlation_id=_resolve_correlation_id(inbox, payload, correlation_id=correlation_id),
            source_system=_non_empty_str(payload.get("source_system")),
            payload=payload,
            attributes=_payload_dict(payload.get("attributes")),
        )

    source_event_type = str(payload.get("event_type") or payload.get("message_type") or "UNKNOWN")
    data = _payload_dict(payload.get("data"))
    canonical_event_type = str(payload.get("canonical_event_type") or source_event_type)
    return NormalizedDeviceEvent(
        source_event_type=source_event_type,
        canonical_event_type=canonical_event_type,
        device_code=_non_empty_str(payload.get("device_code")),
        business_key=_resolve_device_event_business_key(payload, data),
        correlation_id=_resolve_correlation_id(inbox, payload, correlation_id=correlation_id),
        event_time=payload.get("timestamp"),
        payload=payload,
        data=data,
        attributes=_payload_dict(payload.get("attributes")),
    )


__all__ = ["normalize_inbox_input"]
