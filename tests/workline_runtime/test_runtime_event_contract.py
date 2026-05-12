import pytest
from pydantic import ValidationError

from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_runtime_event_carries_monitoring_dimensions():
    event = RuntimeEvent(
        event_type=RuntimeEventType.COMMAND_SUCCEEDED,
        trace_id="trace-1",
        material_run_id=100,
        material_identity_key="pkg:PKG-001",
        workline_id=10,
        device_id=21,
        device_role="WEIGH_SCALE",
        plugin_key="inbound_tote_qc",
        action="WEIGH_TOTE",
        command_id=300,
        duration_ms=1500,
        result="SUCCESS",
        reason_code=None,
        failure_domain=None,
        owner=None,
    )
    assert event.event_type == RuntimeEventType.COMMAND_SUCCEEDED
    assert event.material_run_id == 100
    assert event.material_identity_key == "pkg:PKG-001"
    assert event.workline_id == 10
    assert event.device_id == 21
    assert event.device_role == "WEIGH_SCALE"
    assert event.plugin_key == "inbound_tote_qc"
    assert event.action == "WEIGH_TOTE"
    assert event.command_id == 300
    assert event.duration_ms == 1500
    assert event.result == "SUCCESS"
    assert event.reason_code is None
    assert event.failure_domain is None
    assert event.owner is None


def test_runtime_event_payload_defaults_to_empty_dict():
    event = RuntimeEvent(
        event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
        trace_id="trace-2",
        workline_id=10,
    )
    assert event.payload_json == {}


def test_runtime_event_rejects_unknown_top_level_fields():
    with pytest.raises(ValidationError):
        RuntimeEvent(
            event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
            trace_id="trace-3",
            workline_id=10,
            unexpected_dimension="must-go-through-payload",
        )


def test_runtime_event_payload_default_dict_is_not_shared():
    first = RuntimeEvent(
        event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
        trace_id="trace-4",
        workline_id=10,
    )
    second = RuntimeEvent(
        event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
        trace_id="trace-5",
        workline_id=10,
    )

    first.payload_json["decision"] = "BLOCK"

    assert first.payload_json == {"decision": "BLOCK"}
    assert second.payload_json == {}
