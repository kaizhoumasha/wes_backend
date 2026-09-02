"""Callback 入站事件合同。"""

from __future__ import annotations

from .event_mapper import canonicalize_event_type
from .runtime_events import (
    PLATFORM_CONTROL_EVENTS,
    RESERVED_RUNTIME_EVENTS,
    assert_not_reserved_runtime_event,
    is_platform_control_event,
    is_platform_safety_event,
    is_production_event,
    is_reserved_runtime_event,
)

__all__ = [
    "PLATFORM_CONTROL_EVENTS",
    "RESERVED_RUNTIME_EVENTS",
    "assert_not_reserved_runtime_event",
    "canonicalize_event_type",
    "is_platform_control_event",
    "is_platform_safety_event",
    "is_production_event",
    "is_reserved_runtime_event",
]
