"""Callback 域事件归一化 — wlr.plugin_sdk.normalizers.event_mapper 镜像 (Phase 2 launch PR)。

镜像说明:
- canonicalize_event_type 与 wlr.plugin_sdk.normalizers.event_mapper 行为一致。
- 不再依赖 wlr.plugin_sdk,callback 域内独立维护事件类型归一化规则。
"""

from __future__ import annotations

from typing import Any

from src.utils.value_normalization import optional_str

# 设备事件归一化映射表 (device_event_alias -> canonical_event_type)
_DEVICE_EVENT_ALIASES: dict[str, str] = {
    "scan_completed": "scan_succeeded",
    "scan_done": "scan_succeeded",
    "scan_failed": "scan_failed",
    "scan_error": "scan_failed",
    "induct_completed": "induct_succeeded",
    "induct_done": "induct_succeeded",
    "induct_failed": "induct_failed",
    "divert_completed": "divert_succeeded",
    "divert_done": "divert_succeeded",
    "divert_failed": "divert_failed",
    "fulfill_completed": "fulfill_succeeded",
    "fulfill_done": "fulfill_succeeded",
    "fulfill_failed": "fulfill_failed",
}

# 工作流事件归一化映射表
_WORKFLOW_EVENT_ALIASES: dict[str, str] = {
    "inbound_received": "inbox_received",
    "outbound_received": "outbox_received",
    "session_created": "session_initialized",
    "session_started": "session_initialized",
}


def canonicalize_event_type(event_type: str | None, payload: dict[str, Any] | None = None) -> str | None:
    """将设备/工作流原始事件类型归一化为标准 canonical 名称。

    优先级:
    1. payload 中的 canonical_event_type 显式声明
    2. 设备事件别名表
    3. 工作流事件别名表
    4. 原值 (如果不在任何别名表中,但属于业务生产事件)
    5. None (非字符串或空字符串)
    """

    raw = optional_str(event_type)
    if raw is None:
        return None
    payload_dict = payload or {}
    explicit = optional_str(payload_dict.get("canonical_event_type"))
    if explicit:
        return explicit
    if raw in _DEVICE_EVENT_ALIASES:
        return _DEVICE_EVENT_ALIASES[raw]
    if raw in _WORKFLOW_EVENT_ALIASES:
        return _WORKFLOW_EVENT_ALIASES[raw]
    return raw


__all__ = ["canonicalize_event_type"]
