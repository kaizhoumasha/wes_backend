import pytest

from src.workline_runtime.alerts import build_alerts
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_block_event_generates_actionable_alert():
    alerts = build_alerts(
        [
            RuntimeEvent(
                event_type=RuntimeEventType.PROCESS_BLOCKED,
                trace_id="trace-1",
                material_identity_key="pkg:PKG-001",
                workline_id=10,
                device_id=21,
                device_role="WEIGH_SCALE",
                reason_code="DEVICE_TIMEOUT",
                failure_domain="HARDWARE",
                owner="MAINTENANCE",
                payload_json={"suggested_action": "检查称重设备通讯"},
            )
        ]
    )

    assert len(alerts) == 1
    assert alerts[0].reason_code == "DEVICE_TIMEOUT"
    assert alerts[0].owner == "MAINTENANCE"
    assert alerts[0].suggested_action == "检查称重设备通讯"


def test_non_block_events_do_not_generate_alerts():
    alerts = build_alerts(
        [
            RuntimeEvent(
                event_type=RuntimeEventType.PROCESS_COMPLETED,
                trace_id="trace-1",
                workline_id=10,
            )
        ]
    )

    assert alerts == []


def test_missing_reason_code_defaults_to_unknown():
    alerts = build_alerts(
        [
            RuntimeEvent(
                event_type=RuntimeEventType.PROCESS_BLOCKED,
                trace_id="trace-1",
                workline_id=10,
            )
        ]
    )

    assert len(alerts) == 1
    assert alerts[0].reason_code == "UNKNOWN"


def test_empty_reason_code_is_preserved():
    alerts = build_alerts(
        [
            RuntimeEvent(
                event_type=RuntimeEventType.PROCESS_BLOCKED,
                trace_id="trace-1",
                workline_id=10,
                reason_code="",
            )
        ]
    )

    assert len(alerts) == 1
    assert alerts[0].reason_code == ""


def test_non_string_suggested_action_raises_value_error():
    with pytest.raises(ValueError, match="suggested_action"):
        build_alerts(
            [
                RuntimeEvent(
                    event_type=RuntimeEventType.PROCESS_BLOCKED,
                    trace_id="trace-1",
                    workline_id=10,
                    payload_json={"suggested_action": 123},
                )
            ]
        )
