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


def normalize_inbox_input(inbox: Any, *, correlation_id: str = "") -> Any:
    """按 inbox 类型构建标准化输入模型。"""

    payload = _payload_dict(getattr(inbox, "payload_json", None))
    kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))

    if kind == "COMMAND_RESULT":
        source_result = str(payload.get("result") or "UNKNOWN")
        return NormalizedCommandResult(
            command_code=str(payload.get("command_code") or ""),
            source_result=source_result,
            normalized_result=classify_result(source_result),
            command_type=payload.get("command_type") or payload.get("task_type"),
            device_code=payload.get("device_code"),
            correlation_id=correlation_id or payload.get("correlation_id") or getattr(inbox, "correlation_id", None),
            finish_time=payload.get("finish_time"),
            payload=payload,
            data=_payload_dict(payload.get("data")),
            error_detail=_payload_dict(payload.get("error_detail")),
        )

    if kind == "EXTERNAL_HTTP":
        return NormalizedExternalCallback(
            callback_type=str(payload.get("callback_type") or payload.get("message_type") or "EXTERNAL_HTTP"),
            correlation_id=correlation_id or payload.get("correlation_id") or getattr(inbox, "correlation_id", None),
            source_system=payload.get("source_system"),
            payload=payload,
            attributes=_payload_dict(payload.get("attributes")),
        )

    source_event_type = str(payload.get("event_type") or payload.get("message_type") or "UNKNOWN")
    data = _payload_dict(payload.get("data"))
    return NormalizedDeviceEvent(
        source_event_type=source_event_type,
        canonical_event_type=source_event_type,
        device_code=payload.get("device_code"),
        business_key=payload.get("business_key") or data.get("business_key") or data.get("barcode"),
        correlation_id=correlation_id or payload.get("correlation_id") or getattr(inbox, "correlation_id", None),
        event_time=payload.get("timestamp"),
        payload=payload,
        data=data,
        attributes=_payload_dict(payload.get("attributes")),
    )


__all__ = ["normalize_inbox_input"]
