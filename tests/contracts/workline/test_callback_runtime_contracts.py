from types import SimpleNamespace

import pytest

from src.app.callback.contracts import runtime_events as wlr_runtime_events
from src.app.callback.contracts.event_mapper import canonicalize_event_type
from src.app.callback.contracts.runtime_events import (
    PLATFORM_CONTROL_EVENTS,
    RESERVED_RUNTIME_EVENTS,
    is_platform_control_event,
    is_platform_safety_event,
    is_production_event,
)


def test_callback_event_mapper_uses_workline_runtime_config_mapping() -> None:
    workline = SimpleNamespace(runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "SCAN_COMPLETED"}})

    assert canonicalize_event_type("SCAN_FINISH", workline=workline) == "SCAN_COMPLETED"
    assert canonicalize_event_type("BLOCKED", workline=workline) == "BLOCKED"


@pytest.mark.parametrize(
    ("reserved_target", "expected_message"),
    [
        ("WORKLINE_START_REQUESTED", "WORKLINE_START_REQUESTED 是平台保留控制事件"),
        ("ESTOP_PRESSED", "ESTOP_PRESSED 是平台保留安全事件"),
    ],
)
def test_callback_event_mapper_rejects_reserved_mapping_target(
    reserved_target: str,
    expected_message: str,
) -> None:
    workline = SimpleNamespace(runtime_config_json={"event_type_mapping": {"SCAN_FINISH": reserved_target}})

    with pytest.raises(ValueError, match=expected_message):
        canonicalize_event_type("SCAN_FINISH", workline=workline)


def test_callback_event_mapper_does_not_remap_reserved_event_sources() -> None:
    workline = SimpleNamespace(
        runtime_config_json={
            "event_type_mapping": {
                "ESTOP_PRESSED": "SCAN_COMPLETED",
                "WORKLINE_START_REQUESTED": "SCAN_COMPLETED",
            }
        }
    )

    assert canonicalize_event_type("ESTOP_PRESSED", workline=workline) == "ESTOP_PRESSED"
    assert canonicalize_event_type("WORKLINE_START_REQUESTED", workline=workline) == "WORKLINE_START_REQUESTED"


def test_callback_runtime_events_match_workline_runtime_taxonomy() -> None:
    assert PLATFORM_CONTROL_EVENTS == wlr_runtime_events.PLATFORM_CONTROL_EVENTS
    assert RESERVED_RUNTIME_EVENTS == wlr_runtime_events.RESERVED_RUNTIME_EVENTS

    for event_type in ("WORKLINE_START_REQUESTED", "ESTOP_PRESSED", "SCAN_COMPLETED"):
        assert is_platform_control_event(event_type) is wlr_runtime_events.is_platform_control_event(event_type)
        assert is_platform_safety_event(event_type) is wlr_runtime_events.is_platform_safety_event(event_type)
        assert is_production_event(event_type) is wlr_runtime_events.is_production_event(event_type)

    assert "WORKLINE_START_REQUESTED" in PLATFORM_CONTROL_EVENTS
    assert "ESTOP_PRESSED" in RESERVED_RUNTIME_EVENTS
