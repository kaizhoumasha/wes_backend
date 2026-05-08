from src.app.workline.models.safety import WorkLineRuntimeStatus, WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.models.workline import LineType, WorkLine, WorkLineBase


def test_workline_runtime_status_values_are_minimal_v1() -> None:
    assert [status.value for status in WorkLineRuntimeStatus] == ["READY", "ESTOPPED"]


def test_workline_table_has_safety_projection_but_base_schema_does_not() -> None:
    workline = WorkLine(line_code="WL-SAFE-001", line_name="安全线", line_type=LineType.AUTO)

    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline.active_safety_incident_id is None
    assert "runtime_status" not in WorkLineBase.model_fields
    assert "active_safety_incident_id" not in WorkLineBase.model_fields


def test_safety_incident_defaults_capture_estop_flow() -> None:
    incident = WorklineSafetyIncident(workline_id=1, source_inbox_id=10)

    assert incident.status == WorklineSafetyIncidentStatus.ACTIVE
    assert incident.event_type == "ESTOP_PRESSED"
    assert incident.reason == "ESTOP_PRESSED"
    assert incident.drain_status == "PENDING"
    assert incident.recovery_check_json == {}
