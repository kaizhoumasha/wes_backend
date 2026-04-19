"""设备事件 canonical mapping。"""

from __future__ import annotations

from typing import Any


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def canonicalize_event_type(event_type: str, *, workline: Any | None = None) -> str:
    """将原始 event_type 映射为 canonical_event_type。"""

    runtime_config = _dict_value(getattr(workline, "runtime_config_json", None))
    mapping = _dict_value(runtime_config.get("event_type_mapping"))
    mapped = mapping.get(event_type)
    return mapped if isinstance(mapped, str) and mapped else event_type


__all__ = ["canonicalize_event_type"]
