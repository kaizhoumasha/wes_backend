"""WorkLine runtime 平台保留事件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

RESERVED_RUNTIME_EVENTS = frozenset({"ESTOP_PRESSED"})


def assert_not_reserved_runtime_event(
    event_type: str,
    *,
    owner: str,
    declaration_surface: str | None = None,
) -> None:
    """禁止插件把平台保留事件声明为普通业务事件。"""

    if event_type not in RESERVED_RUNTIME_EVENTS:
        return

    surface = f"（{declaration_surface}）" if declaration_surface else ""
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
    "RESERVED_RUNTIME_EVENTS",
    "assert_no_reserved_runtime_events",
    "assert_not_reserved_runtime_event",
]
