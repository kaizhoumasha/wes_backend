"""Runtime trace response 构造回归测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace

from src.app.device.models.device import Device
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.services.query.runtime_query_service import RuntimeQueryService
from src.app.runtime.orchestration.services.trace.trace_response_builder import (
    _build_command_item,
    build_trace_session_item,
)


def test_trace_session_item_does_not_require_retired_plugin_identity() -> None:
    session = WorklineSession(id=1, session_code="SESSION-001", workline_id=7)

    item = build_trace_session_item(session)

    assert item is not None
    assert "plugin_key" not in type(item).model_fields


def test_both_runtime_command_builders_read_only_final_device_command_fields() -> None:
    command = SimpleNamespace(
        id=11,
        command_code="CMD-11",
        device_code="ROBOT-01",
        line_run_epoch_id=21,
        device_binding_id=31,
        execution_ref_type="OUTBOUND_TASK",
        execution_ref_id="TASK-41",
        contract_key="uniform-device",
        contract_version="v1",
        task_type="MOVE",
        status="ACKNOWLEDGED",
        params={"logical_position": "SLOT-A"},
        payload_digest="a" * 64,
        deadline_at=datetime(2026, 8, 13, tzinfo=UTC),
        trace_id="trace-11",
        attempt_count=1,
        next_attempt_at=None,
        ack_received_at=None,
        completed_at=None,
        result_evidence_id=None,
        failure_code=None,
        reconciliation_reason=None,
    )

    direct = _build_command_item(command)
    query = RuntimeQueryService()._build_command_item(command)

    assert direct == query
    assert direct.device_code == "ROBOT-01"
    assert direct.execution_ref_id == "TASK-41"
    assert direct.attempt_count == 1


def test_runtime_device_builders_do_not_read_retired_mutable_device_state() -> None:
    device = Device(
        id=51,
        device_code="ROBOT-51",
        device_name="机械臂 51",
        device_role="ROBOT_ARM",
        role_index=1,
        work_line_id=7,
    )
    service = RuntimeQueryService()

    summary = service._build_device_summary(device, None, 0, None)
    workline_item = service._build_workline_device_item(device)
    monitor_node = service._build_monitor_device_node(device)

    assert summary.device_status == "UNKNOWN"
    assert workline_item.current_command_id is None
    assert monitor_node.last_heartbeat_at is None
