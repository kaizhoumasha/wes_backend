from types import SimpleNamespace

from src.workline_runtime.plugin_sdk import normalize_inbox_input, resolve_execution_context


def test_resolve_execution_context_uses_workline_defaults_and_device_overrides() -> None:
    workline = SimpleNamespace(
        id=10,
        line_code="WL-A",
        line_name="Line A",
        line_type="AUTO",
        plugin_key="smt_classifier",
        contract_version="1.0",
        workflow_version="2026.04",
        config={"plugin": "config"},
        runtime_config_json={"timeout_policy": {"command": 30}},
        owner_team="wes",
        support_contact="oncall",
        diagnostic_profile={"default_owner_role": "plugin_developer"},
    )
    device = SimpleNamespace(
        id=20,
        device_code="SCANNER-01",
        device_name="Scanner",
        device_role="SCANNER",
        work_line_id=10,
        plugin_key=None,
        contract_version=None,
        protocol="HTTP",
        host="127.0.0.1",
        port=8000,
        timeout=15000,
        callback_path="/api/v1/device/command",
        maintenance_mode=False,
        capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
        diagnostic_profile={"preferred_view": "hardware"},
    )

    runtime = resolve_execution_context(workline, {"SCANNER": [device]})

    assert runtime.workline is not None
    assert runtime.workline.plugin_key == "smt_classifier"
    assert runtime.devices_by_role["SCANNER"][0].plugin_key == "smt_classifier"
    assert runtime.devices_by_role["SCANNER"][0].communication_profile["host"] == "127.0.0.1"


def test_normalize_inbox_input_for_command_result_uses_classifier() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        correlation_id="corr-1",
        payload_json={
            "command_code": "CMD-1",
            "result": "failed",
            "task_type": "PICK_AND_PUT",
            "device_code": "ARM-1",
            "finish_time": 1234567890,
            "data": {"vendor_code": "E100"},
        },
    )

    normalized = normalize_inbox_input(inbox)

    assert normalized.command_code == "CMD-1"
    assert normalized.normalized_result == "TERMINAL_FAILURE"
    assert normalized.data["vendor_code"] == "E100"
