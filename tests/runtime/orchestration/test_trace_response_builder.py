"""Runtime trace response 构造回归测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.app.device.models.device import Device
from src.app.runtime.orchestration.models.runtime import (
    DiagnosisEvidenceHealthResponse,
    DiagnosisVerdictResponse,
    RuntimeWorklineSummary,
    TraceContextResponse,
    TraceDiagnosticContextItem,
)
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.services.query.runtime_query_service import RuntimeQueryService
from src.app.runtime.orchestration.services.trace.trace_query_service import TraceQueryResult, TraceQueryService
from src.app.runtime.orchestration.services.trace.trace_response_builder import (
    _build_command_item,
    _build_diagnostic_item,
    build_trace_session_item,
)
from src.app.workline.trace_context import TraceContext


class _FailedVerdictBuilder:
    def build(self, _result: TraceQueryResult) -> DiagnosisVerdictResponse:
        return DiagnosisVerdictResponse(
            state="failed",
            severity="error",
            title="会话失败",
            summary="会话失败",
            requires_operator_action=True,
            blocking_point="session",
            evidence_health=DiagnosisEvidenceHealthResponse(level="complete", summary="证据完整"),
        )


def test_trace_session_item_does_not_require_retired_plugin_identity() -> None:
    session = WorklineSession(id=1, session_code="SESSION-001", workline_id=7)

    item = build_trace_session_item(session)

    assert item is not None
    assert "plugin_key" not in type(item).model_fields


def test_trace_api_models_exclude_retired_plugin_identity() -> None:
    assert {"plugin_key", "contract_version"}.isdisjoint(TraceContextResponse.model_fields)
    assert "plugin_key" not in TraceDiagnosticContextItem.model_fields
    assert {"plugin_key", "contract_version"}.isdisjoint(RuntimeWorklineSummary.model_fields)


def test_trace_diagnostic_builder_does_not_require_retired_plugin_identity() -> None:
    item = SimpleNamespace(
        request_id="req-1",
        trace_id="trace-1",
        session_id=1,
        inbox_id=2,
        outbox_id=3,
        command_code="CMD-1",
        device_code="ROBOT-1",
        workline_id=7,
        workline_code="LINE-7",
        canonical_event_type="INBOX_FAILED",
        transition="RUNNING->FAILED",
        extra={},
    )

    projected = _build_diagnostic_item(item)

    assert "plugin_key" not in projected.model_dump()


@pytest.mark.parametrize(
    ("failure_code", "expected_error_code"),
    [
        ("WORKFLOW_EXECUTION_FAILED", "WORKFLOW_EXECUTION_FAILED"),
        ("WORKFLOW_TRANSITION_INVALID", "WORKFLOW_TRANSITION_INVALID"),
        ("PLUGIN_BINDING_REQUIRED", "SESSION_RESOLVE_FAILED"),
        ("PLUGIN_EXECUTION_FAILED", "SESSION_RESOLVE_FAILED"),
        ("PLUGIN_TRANSITION_INVALID", "SESSION_RESOLVE_FAILED"),
    ],
)
def test_trace_session_failure_codes_fail_closed_after_plugin_retirement(
    failure_code: str,
    expected_error_code: str,
) -> None:
    session = SimpleNamespace(
        id=1,
        workline_id=7,
        status="FAILED",
        failure_code=failure_code,
        failure_message="会话失败",
    )
    result = TraceQueryResult(
        trace=TraceContext.from_request(trace_id="trace-1"),
        session=session,
        sessions=[session],
    )

    response = TraceQueryService(verdict_builder=_FailedVerdictBuilder())._build_blocking_point(
        result,
        trace_id="trace-1",
    )

    assert response.diagnostic_card.error_code == expected_error_code
    assert response.diagnostic_card.error_domain == "WORKFLOW"


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
