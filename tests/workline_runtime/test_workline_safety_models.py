from pathlib import Path

from src.app.workline.models.safety import WorkLineRuntimeStatus, WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.models.workline import LineType, WorkLine, WorkLineBase


def test_workline_runtime_status_values_include_reconciliation() -> None:
    assert WorkLineRuntimeStatus.STOPPED.value == "STOPPED"
    assert [status.value for status in WorkLineRuntimeStatus] == ["STOPPED", "READY", "RECONCILING", "ESTOPPED"]


def test_workline_table_has_safety_projection_but_base_schema_does_not() -> None:
    workline = WorkLine(line_code="WL-SAFE-001", line_name="安全线", line_type=LineType.AUTO)

    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.active_safety_incident_id is None
    assert workline.start_admission_status is None
    assert workline.start_admission_message is None
    assert workline.start_admission_failed_device_code is None
    assert workline.start_admission_checked_at is None
    assert workline.last_start_request_id is None
    assert workline.last_start_trace_id is None
    assert "runtime_status" not in WorkLineBase.model_fields
    assert "active_safety_incident_id" not in WorkLineBase.model_fields
    assert "start_admission_status" not in WorkLineBase.model_fields


def test_safety_incident_defaults_capture_estop_flow() -> None:
    incident = WorklineSafetyIncident(workline_id=1, source_inbox_id=10)

    assert incident.status == WorklineSafetyIncidentStatus.ACTIVE
    assert incident.event_type == "ESTOP_PRESSED"
    assert incident.reason == "ESTOP_PRESSED"
    assert incident.drain_status == "PENDING"
    assert incident.recovery_check_json == {}


def test_workline_stopped_start_admission_migration_uses_varchar_enum_constraint() -> None:
    migration_paths = sorted(Path("migrations/versions").glob("*_add_workline_stopped_start_admission.py"))

    assert len(migration_paths) == 1
    migration_text = migration_paths[0].read_text(encoding="utf-8")

    assert 'UPGRADE_RUNTIME_STATUS_VALUES = ("STOPPED", "READY", "RECONCILING", "ESTOPPED")' in migration_text
    assert "return sa.Enum(" in migration_text
    assert "native_enum=False" in migration_text
    assert "create_constraint=True" in migration_text
    assert "op.create_check_constraint" in migration_text
    assert "start_admission_status" in migration_text
    assert "start_admission_message" in migration_text
    assert "start_admission_failed_device_code" in migration_text
    assert "start_admission_checked_at" in migration_text
    assert "last_start_request_id" in migration_text
    assert "last_start_trace_id" in migration_text
