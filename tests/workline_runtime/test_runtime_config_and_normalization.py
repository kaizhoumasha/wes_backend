from types import SimpleNamespace

import pytest

from src.workline_runtime.plugin_sdk import normalize_inbox_input, resolve_execution_context

pytestmark = pytest.mark.usefixtures("registered_test_workline_plugin")


def test_resolve_execution_context_uses_workline_defaults_and_device_overrides() -> None:
    workline = SimpleNamespace(
        id=10,
        line_code="WL-A",
        line_name="Line A",
        line_type="AUTO",
        run_mode="SIMULATION",
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        config={"plugin": "config"},
        runtime_config_json={"timeout_policy": {"command": 30}},
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
    assert runtime.workline.plugin_key == "test_workline_plugin"
    assert runtime.workline.run_mode == "SIMULATION"
    assert runtime.devices_by_role["SCANNER"][0].plugin_key == "test_workline_plugin"
    assert runtime.devices_by_role["SCANNER"][0].communication_profile["host"] == "127.0.0.1"


def test_normalize_inbox_input_for_command_result_uses_classifier() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        trace_id="trace-1",
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
    assert normalized.result_classification == "system_failure"
    assert normalized.data["vendor_code"] == "E100"


def test_normalize_inbox_input_infers_command_result_when_kind_missing() -> None:
    inbox = SimpleNamespace(
        kind=None,
        trace_id="trace-2",
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
    assert normalized.result_classification == "system_failure"
    assert normalized.error_detail == {}


def test_normalize_inbox_input_normalizes_whitepaper_error_detail_fields() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        trace_id="trace-3",
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
    assert normalized.result_classification == "hardware_failure"


def test_normalize_inbox_input_uses_runtime_classifier_for_business_ng() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        trace_id="trace-4",
        payload_json={
            "command_code": "CMD-4",
            "result": "SUCCESS",
            "command_type": "MEASUREMENT_REEL",
            "device_code": "MEASURE-1",
            "data": {
                "inspection_result": "NG",
                "reason_code": "INSPECTION_SIZE_NG",
            },
        },
    )

    normalized = normalize_inbox_input(inbox, plugin_key="test_workline_plugin")

    assert normalized.normalized_result == "SUCCESS"
    assert normalized.result_classification == "business_decision"


def test_normalize_inbox_input_treats_failed_inspection_ng_code_as_hardware_failure() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        trace_id="trace-5",
        payload_json={
            "command_code": "CMD-5",
            "result": "FAILED",
            "command_type": "PICK_AND_PUT",
            "device_code": "ARM-1",
            "error_detail": {
                "error_code": "INSPECTION_SIZE_NG",
                "error_message": "料盘尺寸检测异常",
            },
        },
    )

    normalized = normalize_inbox_input(inbox, plugin_key="test_workline_plugin")

    assert normalized.normalized_result == "TERMINAL_FAILURE"
    assert normalized.result_classification == "hardware_failure"


def test_normalize_inbox_input_prefers_canonical_six_in_one_business_key() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="DEVICE_EVENT"),
        trace_id="trace-4",
        payload_json={
            "event_type": "SCAN_COMPLETED",
            "device_code": "SCANNER01",
            "business_key": "UPSTREAM-MISMATCH",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "LotCode": "LOTABC123",
                "DateCode": "20260409",
                "PkgID": "SVYU00125TP4LCR02_2",
            },
        },
    )

    normalized = normalize_inbox_input(inbox)

    import hashlib
    import json

    expected_hash = hashlib.sha256(json.dumps("SVYU00125TP4LCR02_2", ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]

    assert normalized.business_key == expected_hash


def test_normalize_inbox_input_preserves_internal_event_canonical_type() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="INTERNAL_EVENT"),
        trace_id="trace-handoff-1",
        payload_json={
            "message_type": "INTERNAL_EVENT",
            "event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "event_id": "smt-inbound-handoff-source-item:22:claim:2",
            "causation_id": "handoff-source-item:22",
            "trace_id": "trace-handoff-1",
            "data": {
                "handoff_demand_id": 11,
                "handoff_source_item_id": 22,
                "claim_attempt_no": 2,
            },
        },
    )

    normalized = normalize_inbox_input(inbox)

    assert normalized.source_event_type == "SORTING_SOURCE_PICK_REQUESTED"
    assert normalized.canonical_event_type == "SORTING_SOURCE_PICK_REQUESTED"
    assert normalized.trace_id == "trace-handoff-1"
    assert normalized.data["handoff_source_item_id"] == 22


def test_normalize_inbox_input_rejects_malformed_internal_event_payload() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="INTERNAL_EVENT"),
        trace_id="trace-handoff-2",
        payload_json={
            "message_type": "INTERNAL_EVENT",
            "data": {"handoff_source_item_id": 22},
        },
    )

    with pytest.raises(ValueError, match="INTERNAL_EVENT payload missing routable event type"):
        normalize_inbox_input(inbox)


def test_normalize_inbox_input_rejects_internal_event_without_handoff_correlation() -> None:
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="INTERNAL_EVENT"),
        trace_id="trace-handoff-3",
        payload_json={
            "message_type": "INTERNAL_EVENT",
            "event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "event_id": "smt-inbound-handoff-source-item:22:claim:2",
            "causation_id": "handoff-source-item:22",
            "trace_id": "trace-handoff-3",
            "data": {
                "handoff_source_item_id": 22,
                "claim_attempt_no": 2,
            },
        },
    )

    with pytest.raises(ValueError, match="handoff_demand_id"):
        normalize_inbox_input(inbox)
