from types import SimpleNamespace

import pytest

from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.workline_runtime import plugin_manifest as manifest_contract
from src.workline_runtime.plugin_base import WorklinePlugin, on_event
from src.workline_runtime.plugin_sdk import canonicalize_event_type
from src.workline_runtime.runtime_events import (
    PLATFORM_CONTROL_EVENTS,
    RESERVED_RUNTIME_EVENTS,
    assert_not_reserved_runtime_event,
    is_platform_control_event,
    is_platform_safety_event,
    is_production_event,
)


def _contract(name: str):
    return getattr(manifest_contract, name)


def _event_binding(event: str):
    return _contract("EventBinding")(
        event=event,
        source_device_roles=("ENTRY_SCANNER",),
        category=_contract("EventCategory").ENTRY_DEVICE,
    )


def _command_result_event_binding(event: str):
    return _contract("EventBinding")(
        event=event,
        source_device_roles=("ENTRY_SCANNER",),
        category=_contract("EventCategory").COMMAND_RESULT,
    )


def test_estop_is_reserved_runtime_event() -> None:
    assert "ESTOP_PRESSED" in RESERVED_RUNTIME_EVENTS


def test_platform_runtime_event_taxonomy_separates_control_safety_and_production_events() -> None:
    assert is_platform_control_event("WORKLINE_START_REQUESTED") is True
    assert is_platform_safety_event("WORKLINE_START_REQUESTED") is False
    assert is_production_event("WORKLINE_START_REQUESTED") is False

    assert is_platform_control_event("ESTOP_PRESSED") is False
    assert is_platform_safety_event("ESTOP_PRESSED") is True
    assert is_production_event("ESTOP_PRESSED") is False

    assert is_platform_control_event("SCAN_COMPLETED") is False
    assert is_platform_safety_event("SCAN_COMPLETED") is False
    assert is_production_event("SCAN_COMPLETED") is True


def test_workline_start_requested_is_platform_control_only() -> None:
    workline = SimpleNamespace(runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "WORKLINE_START_REQUESTED"}})

    assert "WORKLINE_START_REQUESTED" in PLATFORM_CONTROL_EVENTS
    assert "WORKLINE_START_REQUESTED" not in RESERVED_RUNTIME_EVENTS
    assert is_platform_control_event("WORKLINE_START_REQUESTED") is True
    assert is_platform_safety_event("WORKLINE_START_REQUESTED") is False
    assert is_production_event("WORKLINE_START_REQUESTED") is False
    with pytest.raises(ValueError, match="WORKLINE_START_REQUESTED 是平台保留控制事件"):
        canonicalize_event_type("SCAN_FINISH", workline=workline)


def test_workline_runtime_status_does_not_define_line_idle_state() -> None:
    assert "READY" in WorkLineRuntimeStatus.__members__
    assert "IDLE" not in WorkLineRuntimeStatus.__members__
    assert "LINE_IDLE" not in WorkLineRuntimeStatus.__members__
    assert "WORKLINE_IDLE" not in WorkLineRuntimeStatus.__members__


def test_runtime_event_helper_rejects_estop_with_actionable_message() -> None:
    with pytest.raises(ValueError, match="ESTOP_PRESSED 是平台保留安全事件"):
        assert_not_reserved_runtime_event("ESTOP_PRESSED", owner="test")


def test_runtime_event_helper_rejects_platform_control_start_with_actionable_message() -> None:
    with pytest.raises(ValueError, match="WORKLINE_START_REQUESTED 是平台保留控制事件"):
        assert_not_reserved_runtime_event("WORKLINE_START_REQUESTED", owner="test")


def test_manifest_rejects_reserved_runtime_event_binding_event() -> None:
    with pytest.raises(ValueError, match="ESTOP_PRESSED"):
        _event_binding("ESTOP_PRESSED")


def test_manifest_rejects_reserved_runtime_command_result_event() -> None:
    with pytest.raises(ValueError, match="WORKLINE_START_REQUESTED"):
        _command_result_event_binding("WORKLINE_START_REQUESTED")


def test_on_event_rejects_reserved_event() -> None:
    with pytest.raises(ValueError, match="@on_event"):

        class BadPlugin(WorklinePlugin):
            plugin_key = "bad"
            contract_version = "1.0"

            @on_event("ESTOP_PRESSED")
            async def handle_estop(self, ctx, event):
                raise AssertionError("should not register")


def test_on_event_rejects_platform_control_event() -> None:
    with pytest.raises(ValueError, match="@on_event"):

        class BadPlugin(WorklinePlugin):
            plugin_key = "bad"
            contract_version = "1.0"

            @on_event("WORKLINE_START_REQUESTED")
            async def handle_start(self, ctx, event):
                raise AssertionError("should not register")
