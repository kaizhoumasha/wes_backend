from types import SimpleNamespace

import pytest

from src.app.callback.contracts.event_mapper import canonicalize_event_type
from src.app.callback.contracts.runtime_events import (
    PLATFORM_CONTROL_EVENTS,
    is_platform_control_event,
    is_production_event,
)


def test_callback_event_mapper_uses_workline_runtime_config_mapping() -> None:
    workline = SimpleNamespace(runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "SCAN_COMPLETED"}})

    assert canonicalize_event_type("SCAN_FINISH", workline=workline) == "SCAN_COMPLETED"
    assert canonicalize_event_type("BLOCKED", workline=workline) == "BLOCKED"


def test_callback_event_mapper_rejects_platform_control_mapping_target() -> None:
    workline = SimpleNamespace(runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "WORKLINE_START_REQUESTED"}})

    with pytest.raises(ValueError, match="WORKLINE_START_REQUESTED 是平台保留控制事件"):
        canonicalize_event_type("SCAN_FINISH", workline=workline)


def test_callback_event_mapper_does_not_remap_platform_control_sources() -> None:
    workline = SimpleNamespace(
        runtime_config_json={"event_type_mapping": {"WORKLINE_START_REQUESTED": "SCAN_COMPLETED"}}
    )

    assert canonicalize_event_type("WORKLINE_START_REQUESTED", workline=workline) == "WORKLINE_START_REQUESTED"


def test_callback_runtime_events_keep_only_platform_control_taxonomy() -> None:
    assert "WORKLINE_START_REQUESTED" in PLATFORM_CONTROL_EVENTS
    assert is_platform_control_event("WORKLINE_START_REQUESTED") is True
    assert is_production_event("WORKLINE_START_REQUESTED") is False
    assert is_platform_control_event("SCAN_COMPLETED") is False
    assert is_production_event("SCAN_COMPLETED") is True
