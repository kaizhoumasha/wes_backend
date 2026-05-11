from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.metrics import aggregate_operational_metrics, aggregate_runtime_metrics
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_aggregates_completed_count_and_ng_rate():
    metrics = aggregate_runtime_metrics(
        [
            RuntimeEvent(event_type=RuntimeEventType.PROCESS_COMPLETED, trace_id="t1", workline_id=1),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t2",
                workline_id=1,
                result="NG",
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t3",
                workline_id=1,
                result="OK",
            ),
        ]
    )

    assert metrics.completed_count == 1
    assert metrics.plugin_decision_count == 2
    assert metrics.ng_rate == 0.5


def test_aggregates_command_success_and_timeout_rate():
    metrics = aggregate_runtime_metrics(
        [
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_SUCCEEDED, trace_id="t1", workline_id=1),
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_TIMEOUT, trace_id="t2", workline_id=1),
        ]
    )

    assert metrics.command_count == 2
    assert metrics.command_success_rate == 0.5
    assert metrics.command_timeout_rate == 0.5


def test_zero_denominator_rates_return_zero():
    metrics = aggregate_runtime_metrics([])

    assert metrics.ng_rate == 0.0
    assert metrics.command_success_rate == 0.0
    assert metrics.command_timeout_rate == 0.0


def test_command_failed_counts_as_command_without_success_or_timeout():
    metrics = aggregate_runtime_metrics(
        [
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_FAILED, trace_id="t1", workline_id=1),
        ]
    )

    assert metrics.command_count == 1
    assert metrics.command_success_count == 0
    assert metrics.command_timeout_count == 0
    assert metrics.command_success_rate == 0.0
    assert metrics.command_timeout_rate == 0.0


def test_operational_metrics_calculate_latency_and_plugin_quality():
    metrics = aggregate_operational_metrics(
        events=[
            RuntimeEvent(
                event_type=RuntimeEventType.COMMAND_ACKED,
                trace_id="t1",
                workline_id=1,
                duration_ms=120,
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.COMMAND_SUCCEEDED,
                trace_id="t1",
                workline_id=1,
                duration_ms=900,
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t2",
                workline_id=1,
                result="INVALID_INTENT",
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t3",
                workline_id=1,
                result="ROUTE_UNREACHABLE",
            ),
        ],
        material_runs=[
            MaterialRun(
                run_code="MR-1",
                material_identity_key="pkg:1",
                workline_id=1,
                lifecycle_state=LifecycleState.ACTIVE,
            ),
            MaterialRun(
                run_code="MR-2",
                material_identity_key="pkg:2",
                workline_id=1,
                lifecycle_state=LifecycleState.BLOCKED,
            ),
            MaterialRun(
                run_code="MR-3",
                material_identity_key="pkg:3",
                workline_id=1,
                lifecycle_state=LifecycleState.COMPLETED,
            ),
        ],
    )

    assert metrics.wip_count == 2
    assert metrics.ack_latency_avg_ms == 120
    assert metrics.result_latency_avg_ms == 900
    assert metrics.invalid_intent_count == 1
    assert metrics.route_unreachable_count == 1


def test_operational_metrics_return_none_for_missing_latencies_and_count_wip_states():
    metrics = aggregate_operational_metrics(
        events=[
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_ACKED, trace_id="t1", workline_id=1),
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_SUCCEEDED, trace_id="t2", workline_id=1),
        ],
        material_runs=[
            MaterialRun(
                run_code="MR-1",
                material_identity_key="pkg:1",
                workline_id=1,
                lifecycle_state=LifecycleState.WAITING,
            ),
            MaterialRun(
                run_code="MR-2",
                material_identity_key="pkg:2",
                workline_id=1,
                lifecycle_state=LifecycleState.FAILED,
            ),
            MaterialRun(
                run_code="MR-3",
                material_identity_key="pkg:3",
                workline_id=1,
                lifecycle_state=LifecycleState.CANCELLED,
            ),
        ],
    )

    assert metrics.wip_count == 1
    assert metrics.ack_latency_avg_ms is None
    assert metrics.result_latency_avg_ms is None
