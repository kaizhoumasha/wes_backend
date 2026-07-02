"""Phase 3 benchmark gate definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeBenchmarkScenario:
    """Runtime benchmark scenario contract."""

    name: str
    command: str
    required_metrics: frozenset[str]
    blocks_phase_gate: bool = True


class RuntimeBenchmarkGate:
    """Registry of benchmark scenarios required by Phase 3."""

    def __init__(self, scenarios: list[RuntimeBenchmarkScenario] | None = None) -> None:
        self.scenarios = scenarios or default_phase3_benchmark_scenarios()

    def missing_required(self, available_names: set[str]) -> tuple[str, ...]:
        return tuple(sorted(scenario.name for scenario in self.scenarios if scenario.name not in available_names))


def default_phase3_benchmark_scenarios() -> list[RuntimeBenchmarkScenario]:
    return [
        RuntimeBenchmarkScenario(
            name="runtime_inbox_claim",
            command="uv run pytest tests/load/test_runtime_inbox_claim_benchmark.py -q",
            required_metrics=frozenset({"claim_p95_ms", "duplicate_claim_count"}),
        ),
        RuntimeBenchmarkScenario(
            name="conveyor_queue_writer",
            command="uv run pytest tests/load/test_conveyor_queue_writer_benchmark.py -q",
            required_metrics=frozenset({"write_p95_ms", "reconciling_count"}),
        ),
        RuntimeBenchmarkScenario(
            name="ecs_status_command",
            command="uv run pytest tests/load/test_ecs_status_command_benchmark.py -q",
            required_metrics=frozenset({"status_get_p95_ms", "command_post_p95_ms"}),
        ),
        RuntimeBenchmarkScenario(
            name="plane_snapshot",
            command="uv run pytest tests/load/test_plane_snapshot_benchmark.py -q",
            required_metrics=frozenset({"snapshot_p95_ms", "snapshot_10x_p95_ms"}),
        ),
    ]


runtime_benchmark_gate = RuntimeBenchmarkGate()


__all__ = [
    "RuntimeBenchmarkGate",
    "RuntimeBenchmarkScenario",
    "default_phase3_benchmark_scenarios",
    "runtime_benchmark_gate",
]
