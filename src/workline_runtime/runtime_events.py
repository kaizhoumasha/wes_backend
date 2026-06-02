"""WorkLine runtime 平台保留事件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

PLATFORM_CONTROL_EVENTS = frozenset({"WORKLINE_START_REQUESTED"})
RESERVED_RUNTIME_EVENTS = frozenset({"ESTOP_PRESSED"})


def is_platform_control_event(event_type: str) -> bool:
    """判断事件是否为平台控制事件。"""

    return event_type in PLATFORM_CONTROL_EVENTS


def is_platform_safety_event(event_type: str) -> bool:
    """判断事件是否为平台安全事件。"""

    return event_type in RESERVED_RUNTIME_EVENTS


def is_production_event(event_type: str) -> bool:
    """判断事件是否为普通生产事件。"""

    return not is_platform_control_event(event_type) and not is_platform_safety_event(event_type)


def assert_not_reserved_runtime_event(
    event_type: str,
    *,
    owner: str,
    declaration_surface: str | None = None,
) -> None:
    """禁止插件把平台保留事件声明为普通业务事件。"""

    surface = f"（{declaration_surface}）" if declaration_surface else ""
    if event_type in PLATFORM_CONTROL_EVENTS:
        raise ValueError(f"{event_type} 是平台保留控制事件，不能由 {owner}{surface} 声明或处理")
    if event_type not in RESERVED_RUNTIME_EVENTS:
        return

    raise ValueError(f"{event_type} 是平台保留安全事件，不能由 {owner}{surface} 声明或处理")


def assert_no_reserved_runtime_events(
    event_types: Iterable[str],
    *,
    owner: str,
    declaration_surface: str,
) -> None:
    """批量校验插件声明中的事件类型。"""

    for event_type in event_types:
        assert_not_reserved_runtime_event(
            event_type,
            owner=owner,
            declaration_surface=declaration_surface,
        )


__all__ = [
    "PLATFORM_CONTROL_EVENTS",
    "RESERVED_RUNTIME_EVENTS",
    "assert_no_reserved_runtime_events",
    "assert_not_reserved_runtime_event",
    "is_platform_control_event",
    "is_platform_safety_event",
    "is_production_event",
]
