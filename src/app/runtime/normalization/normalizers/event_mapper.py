# 旧 plugin runtime 镜像实现:src.workline_runtime.plugin_sdk.normalizers.event_mapper 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。
# 自引用 src.workline_runtime.runtime_events 已重定向到 C5a events_bridge。

"""设备事件 canonical mapping。"""

from __future__ import annotations

from typing import Any, cast

from src.app.runtime.orchestration.events_bridge import assert_not_reserved_runtime_event


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(cast("dict[str, Any]", value)) if isinstance(value, dict) else {}


def canonicalize_event_type(event_type: str, *, workline: Any | None = None) -> str:
    """将原始 event_type 映射为 canonical_event_type。"""

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
