"""Callback 域事件归一化 — wlr.plugin_sdk.normalizers.event_mapper 镜像。

镜像说明:
- 生产事件 source 的 event_type_mapping 行为与 wlr.plugin_sdk.normalizers.event_mapper 对齐。
- callback ingress 额外保留平台/安全事件 source 原值,避免 START/ESTOP 被工作线映射改写。
- 不再依赖 wlr.plugin_sdk,callback 域内独立维护事件类型归一化规则。
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
