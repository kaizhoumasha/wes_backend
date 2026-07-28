"""External callback 类型边界。

本模块是 callback 域唯一冻结 allow-set。所有 RuntimeInbox external writer
入口都必须校验 payload 自身类型，不能用独立参数覆盖 payload 中的旧类型。
"""

from __future__ import annotations

from typing import Any

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_TYPED_EFFECT_CALLBACK_TYPES

WMS_ORDINARY_EVENT_TYPES = frozenset(
    {
        "WMS_GRN_RECEIVED",
        "WMS_PALLET_ARRIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PDA_OPERATION_RECORDED",
    }
)
WMS_ALLOWED_CALLBACK_TYPES = WMS_ORDINARY_EVENT_TYPES | WMS_TYPED_EFFECT_CALLBACK_TYPES


def validate_external_callback_type(
    payload: dict[str, Any],
    *,
    declared_callback_type: str | None = None,
    declared_source_system: str | None = None,
) -> str:
    """以 payload 类型为权威，校验独立参数一致性与 WMS 冻结允许集。"""

    payload_callback_type = _first_non_empty_text(payload.get("callback_type"))
    payload_event_type = _first_non_empty_text(payload.get("event_type"))
    if (
        payload_callback_type is not None
        and payload_event_type is not None
        and payload_callback_type != payload_event_type
    ):
        raise ValueError("callback_type does not match payload event_type")

    payload_type = payload_callback_type or payload_event_type
    declared_type = _first_non_empty_text(declared_callback_type)
    if payload_type is not None and declared_type is not None and payload_type != declared_type:
        raise ValueError("callback_type does not match payload")

    callback_type = payload_type or declared_type
    if callback_type is None:
        raise ValueError("callback_type is required")

    payload_source_system = _first_non_empty_text(payload.get("source_system"))
    declared_source = _first_non_empty_text(declared_source_system)
    if payload_source_system is not None and declared_source is not None and payload_source_system != declared_source:
        raise ValueError("source_system does not match payload")
    source_system = payload_source_system or declared_source

    is_wms_family = (
        source_system in {"WMS", "RCS"}
        or callback_type.startswith(("WMS_", "RCS_"))
        or callback_type == "RACK_OPERATION"
    )
    if not is_wms_family:
        return callback_type
    if callback_type not in WMS_ALLOWED_CALLBACK_TYPES:
        raise ValueError(f"callback_type is not allowed: {callback_type}")
    if source_system != "WMS":
        raise ValueError("source_system must be WMS")
    return callback_type


def _first_non_empty_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "WMS_ALLOWED_CALLBACK_TYPES",
    "WMS_ORDINARY_EVENT_TYPES",
    "validate_external_callback_type",
]
