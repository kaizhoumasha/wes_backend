"""Runtime metrics derived from append-only runtime facts."""

from __future__ import annotations

from dataclasses import dataclass

from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


@dataclass(frozen=True)
class RuntimeMetrics:
    completed_count: int = 0
    plugin_decision_count: int = 0
    ng_decision_count: int = 0
    command_count: int = 0
    command_success_count: int = 0
    command_timeout_count: int = 0

    @property
    def ng_rate(self) -> float:
        if self.plugin_decision_count == 0:
            return 0.0
        return self.ng_decision_count / self.plugin_decision_count

    @property
    def command_success_rate(self) -> float:
        if self.command_count == 0:
            return 0.0
        return self.command_success_count / self.command_count

    @property
    def command_timeout_rate(self) -> float:
        if self.command_count == 0:
            return 0.0
        return self.command_timeout_count / self.command_count


@dataclass(frozen=True)
class OperationalMetrics:
    wip_count: int = 0
    ack_latency_avg_ms: int | None = None
    result_latency_avg_ms: int | None = None
    invalid_intent_count: int = 0
    route_unreachable_count: int = 0


def _avg(values: list[int]) -> int | None:
    if not values:
        return None
    return int(sum(values) / len(values))


def aggregate_runtime_metrics(events: list[RuntimeEvent]) -> RuntimeMetrics:
    completed_count = 0
    plugin_decision_count = 0
    ng_decision_count = 0
    command_count = 0
    command_success_count = 0
    command_timeout_count = 0

    for event in events:
        if event.event_type == RuntimeEventType.PROCESS_COMPLETED:
            completed_count += 1

        if event.event_type == RuntimeEventType.PLUGIN_DECISION_MADE:
            plugin_decision_count += 1
            if event.result == "NG":
                ng_decision_count += 1

        if event.event_type in {
            RuntimeEventType.COMMAND_SUCCEEDED,
            RuntimeEventType.COMMAND_FAILED,
            RuntimeEventType.COMMAND_TIMEOUT,
        }:
            command_count += 1

        if event.event_type == RuntimeEventType.COMMAND_SUCCEEDED:
            command_success_count += 1

        if event.event_type == RuntimeEventType.COMMAND_TIMEOUT:
            command_timeout_count += 1

    return RuntimeMetrics(
        completed_count=completed_count,
        plugin_decision_count=plugin_decision_count,
        ng_decision_count=ng_decision_count,
        command_count=command_count,
        command_success_count=command_success_count,
        command_timeout_count=command_timeout_count,
    )


def aggregate_operational_metrics(
    *, events: list[RuntimeEvent], material_runs: list[MaterialRun]
) -> OperationalMetrics:
    ack_latencies: list[int] = []
    result_latencies: list[int] = []
    invalid_intent_count = 0
    route_unreachable_count = 0

    for event in events:
        if event.event_type == RuntimeEventType.COMMAND_ACKED and event.duration_ms is not None:
            ack_latencies.append(event.duration_ms)

        if event.event_type == RuntimeEventType.COMMAND_SUCCEEDED and event.duration_ms is not None:
            result_latencies.append(event.duration_ms)

        if event.event_type == RuntimeEventType.PLUGIN_DECISION_MADE:
            if event.result == "INVALID_INTENT":
                invalid_intent_count += 1
            if event.result == "ROUTE_UNREACHABLE":
                route_unreachable_count += 1

    wip_states = {LifecycleState.ACTIVE, LifecycleState.WAITING, LifecycleState.BLOCKED}
    wip_count = sum(1 for material_run in material_runs if material_run.lifecycle_state in wip_states)

    return OperationalMetrics(
        wip_count=wip_count,
        ack_latency_avg_ms=_avg(ack_latencies),
        result_latency_avg_ms=_avg(result_latencies),
        invalid_intent_count=invalid_intent_count,
        route_unreachable_count=route_unreachable_count,
    )


__all__ = [
    "OperationalMetrics",
    "RuntimeMetrics",
    "aggregate_operational_metrics",
    "aggregate_runtime_metrics",
]
