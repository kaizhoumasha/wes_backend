from types import SimpleNamespace

import pytest

from src.app.device.models import DeviceCapabilityProfile, parse_device_capabilities
from src.app.device.services.device_service import DeviceService
from src.app.workline.services.workline_service import WorkLineService
from src.workline_runtime.plugin_sdk import canonicalize_event_type


def test_device_capability_profile_is_permissive_by_default() -> None:
    profile = DeviceCapabilityProfile()

    assert profile.supports_event("SCAN_COMPLETED") is True
    assert profile.supports_command("PICK_AND_PUT") is True
    assert profile.allows_result_callback() is True


def test_device_capability_profile_enforces_configured_lists() -> None:
    profile = parse_device_capabilities(
        {
            "supports_event_types": ["SCAN_COMPLETED"],
            "supports_command_types": ["PICK_AND_PUT"],
            "supports_result_callback": False,
        }
    )

    assert profile.supports_event("SCAN_COMPLETED") is True
    assert profile.supports_event("BLOCKED") is False
    assert profile.supports_command("PICK_AND_PUT") is True
    assert profile.supports_command("MOVE_FORWARD") is False
    assert profile.allows_result_callback() is False


def test_canonicalize_event_type_uses_workline_runtime_mapping() -> None:
    workline = SimpleNamespace(runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "SCAN_COMPLETED"}})

    assert canonicalize_event_type("SCAN_FINISH", workline=workline) == "SCAN_COMPLETED"
    assert canonicalize_event_type("BLOCKED", workline=workline) == "BLOCKED"


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        ({"capabilities_json": []}, "Input should be a valid dictionary"),
        ({"runtime_config_json": []}, "runtime_config_json 必须为对象"),
        ({"runtime_config_json": {"event_type_mapping": []}}, "runtime_config_json.event_type_mapping 必须为对象"),
    ],
)
def test_schema_validators_reject_invalid_shapes(payload: dict, expected_message: str) -> None:
    if "capabilities_json" in payload:
        with pytest.raises(TypeError, match=expected_message):
            DeviceService._validate_capabilities(payload)
        return

    with pytest.raises(TypeError, match=expected_message):
        WorkLineService._validate_runtime_config(payload)
