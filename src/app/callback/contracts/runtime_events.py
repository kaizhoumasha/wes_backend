"""Callback 域 runtime events 常量与判定函数。

这些集合是 callback 入站分类的本地合同，跨域交互只使用事件字符串，
不反向依赖 runtime orchestration 实现。
"""

from __future__ import annotations

PLATFORM_CONTROL_EVENTS: frozenset[str] = frozenset({"WORKLINE_START_REQUESTED"})


def is_platform_control_event(event_type: str | None) -> bool:
    """是否为平台控制事件 (由 platform/admin 触发的设备控制指令)。"""

    return isinstance(event_type, str) and event_type in PLATFORM_CONTROL_EVENTS


def is_production_event(event_type: str | None) -> bool:
    """是否属于业务生产事件 (非平台控制)。"""

    if not isinstance(event_type, str):
        return False
    return not is_platform_control_event(event_type)


def assert_not_platform_control_event(
    event_type: str | None,
    *,
    owner: str,
    declaration_surface: str | None = None,
) -> None:
    """禁止插件把平台控制事件声明为普通业务事件。"""

    if not isinstance(event_type, str):
        return

    surface = f"（{declaration_surface}）" if declaration_surface else ""
    if event_type in PLATFORM_CONTROL_EVENTS:
        raise ValueError(f"{event_type} 是平台保留控制事件，不能由 {owner}{surface} 声明或处理")


__all__ = [
    "PLATFORM_CONTROL_EVENTS",
    "assert_not_platform_control_event",
    "is_platform_control_event",
    "is_production_event",
]
