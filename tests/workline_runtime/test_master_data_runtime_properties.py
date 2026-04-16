from src.app.device.models.device import Device, DeviceProtocol, DeviceStatus
from src.app.workline.models.workline import LineType, WorkLine


def test_workline_runtime_and_diagnostic_properties() -> None:
    workline = WorkLine(
        line_code="WL-01",
        line_name="Line 01",
        line_type=LineType.AUTO,
        plugin_key="smt_classifier",
        contract_version="1.0",
        workflow_version="2026.04",
        runtime_config_json={"retry_policy": {"max": 3}},
        owner_team="wes",
        support_contact="oncall",
        diagnostic_profile={"default_owner_role": "plugin_developer"},
    )

    assert workline.resolved_runtime_config["plugin_key"] == "smt_classifier"
    assert workline.diagnostic_summary["owner_team"] == "wes"


def test_device_communication_profile_is_runtime_friendly() -> None:
    device = Device(
        device_code="SCANNER-01",
        device_name="Scanner",
        device_role="SCANNER",
        protocol=DeviceProtocol.HTTP,
        device_status=DeviceStatus.IDLE,
        host="127.0.0.1",
        port=8000,
        timeout=15000,
        callback_path="/api/v1/device/command",
    )

    assert device.communication_profile["protocol"] == DeviceProtocol.HTTP.value
    assert device.communication_profile["host"] == "127.0.0.1"
    assert device.communication_profile["callback_path"] == "/api/v1/device/command"
