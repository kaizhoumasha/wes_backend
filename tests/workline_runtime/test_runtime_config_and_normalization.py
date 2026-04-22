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
        config={"plugin": "config"},
        runtime_config_json={"timeout_policy": {"command": 30}},
        owner_team="wes",
        support_contact="oncall",
        diagnostic_profile={"summary_mode": "compact"},
    )
    device = SimpleNamespace(
        id=20,
        device_code="SCANNER-01",
        device_name="Scanner",
        device_role="SCANNER",
        work_line_id=10,
        protocol="HTTP",
        host="127.0.0.1",
        port=8000,
        timeout=15000,
        callback_path="/api/v1/device/command",
        maintenance_mode=False,
        capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
        diagnostic_profile={"view": "hardware"},
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


def test_normalize_inbox_input_infers_command_result_when_kind_missing() -> None:
    inbox = SimpleNamespace(
        kind=None,
        correlation_id="corr-2",
        payload_json={
            "command_code": "CMD-2",
            "result": "ERROR",
            "command_type": "PICK_AND_PUT",
            "device_code": "ARM-2",
            "error_code": "ARM_ERROR",
            "error_message": "机械臂错误",
        },
    )

    normalized = normalize_inbox_input(inbox)

    assert normalized.command_code == "CMD-2"
    assert normalized.normalized_result == "TERMINAL_FAILURE"
    assert normalized.error_detail == {
        "error_code": "ARM_ERROR",
        "error_message": "机械臂错误",
    }


def test_normalize_inbox_input_normalizes_whitepaper_error_detail_fields() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        correlation_id="corr-3",
        payload_json={
            "command_code": "CMD-3",
            "result": "FAILED",
            "command_type": "PICK_AND_PUT",
            "device_code": "ARM-3",
            "error_detail": {
                "code": "BIN_FULL",
                "msg": "料箱已满",
            },
        },
    )

    normalized = normalize_inbox_input(inbox)

    assert normalized.command_code == "CMD-3"
    assert normalized.error_detail["code"] == "BIN_FULL"
    assert normalized.error_detail["msg"] == "料箱已满"
    assert normalized.error_detail["error_code"] == "BIN_FULL"
    assert normalized.error_detail["error_message"] == "料箱已满"
