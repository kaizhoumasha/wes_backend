"""BC-02 Runtime Snapshot 行为契约。

验收: active session 可查询 state、timeline、inbox、hold、pending intent、correlation。
"""

from __future__ import annotations

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline
from src.app.runtime.orchestration.services.runtime_snapshot_assembler import (
    RuntimeSnapshotInput,
    runtime_snapshot_assembler,
)


def test_runtime_snapshot_exposes_state_timeline_inbox_hold_intent_correlation():
    """目标态: snapshot 必须含 state/timeline/inbox/hold/pending intent/correlation。"""
    session = ExecutionSession(
        id=101,
        workline_id=7,
        state="RUNNING",
    )
    correlation = ExecutionCorrelation(
        id=201,
        correlation_id="corr-001",
        execution_session_id=101,
        trace_id="trace-001",
        source_event_id="evt-001",
        business_owner_key="workline:7",
    )
    inbox = RuntimeInbox(
        id=301,
        execution_session_id=101,
        correlation_id="corr-001",
        provider_code="WMS",
        event_type="BIN_ARRIVED",
        source_event_id="evt-001",
        status="RECEIVED",
    )
    hold = RuntimeHold(
        id=401,
        execution_session_id=101,
        correlation_id="corr-001",
        reason="等待扫码",
        hold_type="RESOURCE_WAIT",
        scope_type="WORK_ITEM",
        scope_key="wi-001",
    )
    pending_intent = RuntimeIntentLog(
        id=501,
        execution_session_id=101,
        correlation_id="corr-001",
        provider_code="ECS",
        target_domain="device",
        target_action="dispatch_command",
        operation_kind="DEVICE_DISPATCH",
        idempotency_key="WES-DEVICE-001",
        request_hash="sha256-001",
        dispatch_key="device-command:001",
    )
    timeline = RuntimeTimeline(
        id=601,
        execution_session_id=101,
        trace_id="trace-001",
        correlation_id="corr-001",
        event_type="INBOX_RECEIVED",
        occurred_at=1700000000000,
    )

    snapshot = runtime_snapshot_assembler.assemble(
        RuntimeSnapshotInput(
            session=session,
            correlation=correlation,
            timeline=(timeline,),
            inbox=(inbox,),
            hold=(hold,),
            pending_intent=(pending_intent,),
        )
    )

    assert set(snapshot) == {"state", "timeline", "inbox", "hold", "pending_intent", "correlation"}
    assert "manifest_version" not in snapshot["state"]
    assert snapshot["state"]["state"] == "RUNNING"
    assert snapshot["timeline"][0]["event_type"] == "INBOX_RECEIVED"
    assert snapshot["inbox"][0]["status"] == "RECEIVED"
    assert snapshot["hold"][0]["scope_key"] == "wi-001"
    assert snapshot["pending_intent"][0]["effect_status"] == "PROPOSED"
    assert snapshot["pending_intent"][0]["dispatch_key"] == "device-command:001"
    assert snapshot["correlation"]["correlation_id"] == "corr-001"
