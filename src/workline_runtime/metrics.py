"""Runtime metrics derived from append-only runtime facts."""

from __future__ import annotations

from dataclasses import dataclass

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


__all__ = ["RuntimeMetrics", "aggregate_runtime_metrics"]
