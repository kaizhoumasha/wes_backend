# 旧 plugin runtime 镜像实现:src.workline_runtime.plugin_sdk.normalizers.input_normalizer 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。
# 自引用 src.workline_runtime.{contracts, plugin_sdk.classifiers, plugin_sdk.contracts, utils}
# 已重定向到 src.app.workline.domain.contracts / src.app.runtime.normalization.
# {classifiers,contracts} / src.app.workline.utils。

"""Inbox 输入标准化。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.capabilities.material_flow.contracts.six_in_one import SixInOne
from src.app.runtime.normalization.contracts import (
    NormalizedDeviceEvent,
    NormalizedExternalCallback,
)
from src.app.workline.utils import non_empty_str, payload_dict


def _infer_kind(raw_kind: Any, payload: dict[str, Any]) -> Any:
    """在 kind 缺失时，按 payload 形状做最小推断。

    注意：只有在 inbox.kind 缺失/为空时才推断。
    """

    kind = getattr(raw_kind, "value", raw_kind)
    if isinstance(kind, str) and kind:
        return kind

    if payload.get("callback_type") or payload.get("source_system"):
        return "EXTERNAL_HTTP"

    return kind


def _resolve_trace_id(inbox: Any, payload: dict[str, Any], *, trace_id: str = "") -> str | None:
    return (
        non_empty_str(trace_id)
        or non_empty_str(payload.get("trace_id"))
        or non_empty_str(getattr(inbox, "trace_id", None))
    )


def _resolve_device_event_business_key(
    payload: dict[str, Any],
    data: dict[str, Any],
) -> str | None:
    canonical_six_in_one = SixInOne.model_validate(data)
    if canonical_six_in_one.business_key:
        return canonical_six_in_one.business_key

    return (
        non_empty_str(payload.get("business_key"))
        or non_empty_str(data.get("business_key"))
        or non_empty_str(data.get("barcode"))
    )


def _is_internal_event(kind: Any, payload: dict[str, Any]) -> bool:
    message_type = payload.get("message_type")
    return message_type == "INTERNAL_EVENT" or (kind == "INTERNAL_EVENT" and message_type != "MANUAL_OPERATION")


def _require_canonical_event_type(inbox: Any) -> str:
    """读取 RuntimeInbox 持久化的 canonical 事件类型。"""
    event_type = non_empty_str(getattr(inbox, "event_type", None))
    if event_type is None:
        raise ValueError("RuntimeInbox event_type is required")
    return event_type


def _normalize_internal_event(
    inbox: Any,
    payload: dict[str, Any],
    *,
    trace_id: str,
) -> Any:
    source_event_type = non_empty_str(payload.get("event_type"))
    canonical_event_type = _require_canonical_event_type(inbox)
    if source_event_type is None:
        source_event_type = canonical_event_type

    data_value = payload.get("data")
    if not isinstance(data_value, dict):
        raise TypeError("INTERNAL_EVENT payload data must be an object")

    event_id = non_empty_str(getattr(inbox, "event_id", None)) or non_empty_str(payload.get("event_id"))
    causation_id = non_empty_str(getattr(inbox, "causation_id", None)) or non_empty_str(payload.get("causation_id"))
    if event_id is None:
        raise ValueError("INTERNAL_EVENT payload missing event_id")
    if causation_id is None:
        raise ValueError("INTERNAL_EVENT payload missing causation_id")
    resolved_trace_id = _resolve_trace_id(inbox, payload, trace_id=trace_id)
    if not resolved_trace_id:
        raise ValueError("INTERNAL_EVENT payload missing trace_id")

    # INTERNAL_EVENT 的规范元数据属于 RuntimeInbox 列；标准化输出补成完整
    # envelope，避免业务 handler 误依赖生产者重复写入 payload。
    normalized_payload = dict(payload)
    normalized_payload.update(
        {
            "event_id": event_id,
            "causation_id": causation_id,
            "trace_id": resolved_trace_id,
        }
    )
    data = payload_dict(data_value)
    return NormalizedDeviceEvent(
        source_event_type=source_event_type,
        canonical_event_type=canonical_event_type,
        device_code=non_empty_str(payload.get("device_code")),
        business_key=_resolve_device_event_business_key(
            payload,
            data,
        ),
        trace_id=resolved_trace_id,
        event_time=payload.get("timestamp"),
        payload=normalized_payload,
        data=data,
        attributes=payload_dict(payload.get("attributes")),
    )


def normalize_inbox_input(
    inbox: Any,
    *,
    trace_id: str = "",
) -> Any:
    """按 inbox 类型构建标准化输入模型。"""

    payload = payload_dict(getattr(inbox, "payload_json", None))
    kind = _infer_kind(getattr(inbox, "kind", None), payload)
    if _is_internal_event(kind, payload):
        return _normalize_internal_event(
            inbox,
            payload,
            trace_id=trace_id,
        )

    if kind == "EXTERNAL_HTTP":
        attributes = payload_dict(payload.get("attributes"))
        return NormalizedExternalCallback(
            callback_type=str(payload.get("callback_type") or payload.get("message_type") or "EXTERNAL_HTTP"),
            runtime_capability=non_empty_str(payload.get("runtime_capability"))
            or non_empty_str(attributes.get("runtime_capability")),
            trace_id=_resolve_trace_id(inbox, payload, trace_id=trace_id),
            source_system=non_empty_str(payload.get("source_system")),
            payload=payload,
            attributes=attributes,
        )

    source_event_type = str(payload.get("event_type") or payload.get("message_type") or "UNKNOWN")
    data = payload_dict(payload.get("data"))
    canonical_event_type = _require_canonical_event_type(inbox)
    return NormalizedDeviceEvent(
        source_event_type=source_event_type,
        canonical_event_type=canonical_event_type,
        device_code=non_empty_str(payload.get("device_code")),
        business_key=_resolve_device_event_business_key(
            payload,
            data,
        ),
        trace_id=_resolve_trace_id(inbox, payload, trace_id=trace_id),
        event_time=payload.get("timestamp"),
        payload=payload,
        data=data,
        attributes=payload_dict(payload.get("attributes")),
    )


__all__ = ["normalize_inbox_input"]
