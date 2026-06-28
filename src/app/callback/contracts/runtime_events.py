"""Callback 域 runtime events 常量与判定函数 — wlr.runtime_events 镜像 (Phase 2 launch PR)。

镜像说明:
- PLATFORM_CONTROL_EVENTS / RESERVED_RUNTIME_EVENTS 集合与 wlr.runtime_events
  保持完全一致 (跨域按字符串比对)。
- is_platform_control_event / is_reserved_runtime_event / is_production_event
  行为与 wlr 一致,无外部依赖。
"""

from __future__ import annotations

PLATFORM_CONTROL_EVENTS: frozenset[str] = frozenset(
    {
        # 设备故障复位指令
        "device_reset",
        "device_recover",
        # 工作线协调指令
        "workline_pause",
        "workline_resume",
        "workline_isolate",
        # 平台健康指令
        "platform_heartbeat",
        "platform_drain",
    }
)


RESERVED_RUNTIME_EVENTS: frozenset[str] = frozenset(
    {
        # 工作线内部调度事件(由 runtime 自身产生,插件不可消费)
        "runtime_session_advance",
        "runtime_workitem_step",
        "runtime_inbox_claim",
        "runtime_inbox_process",
        "runtime_inbox_retry",
        "runtime_inbox_dead_letter",
        "runtime_timeline_query",
        "runtime_intent_log_dispatch",
        "runtime_intent_log_replay",
        "runtime_hold_evaluate",
    }
)


def is_platform_control_event(event_type: str | None) -> bool:
    """是否为平台控制事件 (由 platform/admin 触发的设备控制指令)。"""

    return isinstance(event_type, str) and event_type in PLATFORM_CONTROL_EVENTS


def is_reserved_runtime_event(event_type: str | None) -> bool:
    """是否为 runtime 内部保留事件 (插件不可消费,仅 runtime 自治)。"""

    return isinstance(event_type, str) and event_type in RESERVED_RUNTIME_EVENTS


def is_production_event(event_type: str | None) -> bool:
    """是否属于业务生产事件 (非平台控制,非 runtime 保留)。"""

    if not isinstance(event_type, str):
        return False
    if event_type in PLATFORM_CONTROL_EVENTS:
        return False
    return event_type not in RESERVED_RUNTIME_EVENTS


def assert_not_reserved_runtime_event(event_type: str | None) -> None:
    """断言给定 event_type 不在 runtime 保留集合中,否则抛 ValueError。"""

    if is_reserved_runtime_event(event_type):
        raise ValueError(f"event_type '{event_type}' 属于 runtime 保留事件,不允许插件或外部调用方消费")


def is_platform_safety_event(event_type: str | None) -> bool:
    """是否为平台安全事件 (强制暂停、隔离、人为介入)。

    平台安全事件是 PLATFORM_CONTROL_EVENTS 的子集,但要求操作员显式授权。
    """

    safety_subset = {"workline_pause", "workline_isolate", "platform_drain"}
    return isinstance(event_type, str) and event_type in safety_subset


__all__ = [
    "PLATFORM_CONTROL_EVENTS",
    "RESERVED_RUNTIME_EVENTS",
    "assert_not_reserved_runtime_event",
    "is_platform_control_event",
    "is_platform_safety_event",
    "is_production_event",
    "is_reserved_runtime_event",
]
