"""Phase 3 P0 technical closure contract."""

from __future__ import annotations

from datetime import timedelta

from src.utils.timezone import timezone


def test_phase3_p0_minimal_chain_records_runtime_effects_and_reconciliation() -> None:
    """P0 闭环必须串起 manifest/session/inbox/intent/device/WMS/plane/reconciliation。"""

    from src.app.runtime.orchestration.scenario_replay import (
        ScenarioEvent,
        ScenarioRecorder,
        ScenarioReplayRunner,
    )

    events = [
        ScenarioEvent(
            event_id="manifest-001",
            kind="workline_manifest",
            occurred_at="2026-07-02T10:00:00Z",
            payload={"object_key": "workline:WL-1", "state": "ACTIVE"},
        ),
        ScenarioEvent(
            event_id="session-001",
            kind="execution_session",
            occurred_at="2026-07-02T10:00:01Z",
            payload={"object_key": "session:S-1", "state": "RUNNING"},
        ),
        ScenarioEvent(
            event_id="inbox-001",
            kind="runtime_inbox",
            occurred_at="2026-07-02T10:00:02Z",
            payload={"source_event_id": "ecs-scan-1", "object_key": "pkg:PKG-0001", "state": "RECEIVED"},
        ),
        ScenarioEvent(
            event_id="intent-001",
            kind="runtime_intent",
            occurred_at="2026-07-02T10:00:03Z",
            payload={"effect_key": "device-command:CMD-1", "object_key": "pkg:PKG-0001", "state": "DISPATCHING"},
        ),
        ScenarioEvent(
            event_id="device-001",
            kind="device_command",
            occurred_at="2026-07-02T10:00:04Z",
            payload={"effect_key": "device-command:CMD-1", "object_key": "pkg:PKG-0001", "state": "ACKED"},
        ),
        ScenarioEvent(
            event_id="wms-001",
            kind="wms_fulfillment",
            occurred_at="2026-07-02T10:00:05Z",
            payload={"effect_key": "wms-fulfillment:WMS-1", "object_key": "pkg:PKG-0001", "state": "SUCCEEDED"},
        ),
        ScenarioEvent(
            event_id="plane-001",
            kind="plane_snapshot",
            occurred_at="2026-07-02T10:00:06Z",
            payload={"object_key": "pkg:PKG-0001", "state": "VISIBLE"},
        ),
        ScenarioEvent(
            event_id="recon-001",
            kind="runtime_conflict",
            occurred_at="2026-07-02T10:00:07Z",
            payload={"object_key": "pkg:PKG-0001", "state": "RECONCILING", "reason": "callback_out_of_order"},
        ),
    ]

    recording = ScenarioRecorder().record(scenario_id="phase3-p0-minimal", events=events)
    result = ScenarioReplayRunner().replay(recording)

    assert result.timeline == tuple(f"{event.kind}:{event.event_id}" for event in recording.events)
    assert result.outbox_effect_keys == ("device-command:CMD-1", "wms-fulfillment:WMS-1")
    assert result.reconciliation_reasons == ("callback_out_of_order",)
    assert len(result.projection_hash) == 64


def test_phase3_device_timeout_and_wms_reject_enter_reconciling_without_silent_success() -> None:
    """ECS 超时与 WMS 拒绝必须进入 RECONCILING, 不能静默成功。"""

    from src.app.reconciliation.manager import ReconciliationConflictInput, ReconciliationManager
    from src.app.runtime.orchestration.services.device_dispatch_policy import (
        DeviceDispatchDecisionKind,
        DeviceDispatchPolicy,
        DeviceDispatchRequest,
        DeviceRuntimeSnapshot,
        DeviceRuntimeStatus,
    )
    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    now = timezone.now_for_db()
    device_decision = DeviceDispatchPolicy().evaluate(
        DeviceDispatchRequest(
            command_code="CMD-TIMEOUT",
            device_role="scanner",
            capability_code="SCAN",
            dispatch_deadline_at=now,
        ),
        snapshot=DeviceRuntimeSnapshot(
            device_code="DEV-1",
            status=DeviceRuntimeStatus.RUNNING,
            observed_at=now,
            status_valid_until=now + timedelta(milliseconds=1000),
        ),
        now=now + timedelta(milliseconds=1),
    )
    wms_result = WmsFulfillmentStateMachine().transition(
        current=FulfillmentState.SENT,
        event=FulfillmentEvent.PROVIDER_REJECTED,
        now=now,
    )
    reconciliation = ReconciliationManager().register_conflict(
        ReconciliationConflictInput(
            owner_domain="runtime",
            owner_kind="ExecutionSession",
            owner_id="session-timeout",
            conflict_kind="DEVICE_TIMEOUT_WMS_REJECT",
            reason="device timeout and WMS business reject require manual resolution",
            evidence_refs=["device:CMD-TIMEOUT", "wms:reject"],
            detected_at=now,
        )
    )

    assert device_decision.kind == DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD
    assert device_decision.runtime_hold_required is True
    assert wms_result.state == FulfillmentState.REJECTED
    assert reconciliation.runtime_hold_required is True
    assert reconciliation.status == "PENDING"


def test_phase3_benchmark_gate_lists_all_required_runtime_scenarios() -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    gate = RuntimeBenchmarkGate()

    assert gate.missing_required({"runtime_inbox_claim"}) == (
        "conveyor_queue_writer",
        "ecs_status_command",
        "plane_snapshot",
    )
    assert (
        gate.missing_required(
            {
                "runtime_inbox_claim",
                "conveyor_queue_writer",
                "ecs_status_command",
                "plane_snapshot",
            }
        )
        == ()
    )
