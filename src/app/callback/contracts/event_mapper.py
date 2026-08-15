"""Callback 域事件归一化。

生产事件使用工作线 ``event_type_mapping``；平台与安全事件保留 source 原值，
避免 START/ESTOP 被工作线映射改写。
"""

from __future__ import annotations

from typing import Any, cast

from .runtime_events import assert_not_reserved_runtime_event, is_production_event


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(cast("dict[str, Any]", value)) if isinstance(value, dict) else {}


def canonicalize_event_type(event_type: str, *, workline: Any | None = None) -> str:
    """将原始 event_type 映射为 canonical_event_type。"""

    if not is_production_event(event_type):
        return event_type

    runtime_config = _dict_value(getattr(workline, "runtime_config_json", None))
    mapping = _dict_value(runtime_config.get("event_type_mapping"))
    mapped = mapping.get(event_type)
    if not isinstance(mapped, str) or not mapped:
        return event_type

    assert_not_reserved_runtime_event(
        mapped,
        owner="runtime_config_json.event_type_mapping",
        declaration_surface=f"{event_type} 的映射目标",
    )
    return mapped


__all__ = ["canonicalize_event_type"]
