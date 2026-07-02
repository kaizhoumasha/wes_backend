"""Phase 3 benchmark gate definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeBenchmarkScenario:
    """Runtime benchmark scenario contract."""

    name: str
    command: str
    required_metrics: frozenset[str]
    blocks_phase_gate: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeBenchmarkArtifactValidation:
    """Validation result for a structured Phase 3 benchmark artifact."""

    valid: bool
    reason: str = "OK"
    missing_scenarios: tuple[str, ...] = ()
    missing_metrics: tuple[str, ...] = ()
    missing_thresholds: tuple[str, ...] = ()
    failed_thresholds: tuple[str, ...] = ()
    invalid_sample_counts: tuple[str, ...] = ()


class RuntimeBenchmarkGate:
    """Registry of benchmark scenarios required by Phase 3."""

    def __init__(self, scenarios: list[RuntimeBenchmarkScenario] | None = None) -> None:
        self.scenarios = scenarios or default_phase3_benchmark_scenarios()

    def missing_required(self, available_names: set[str]) -> tuple[str, ...]:
        return tuple(sorted(scenario.name for scenario in self.scenarios if scenario.name not in available_names))

    def validate_artifact(self, artifact: Mapping[str, object]) -> RuntimeBenchmarkArtifactValidation:
        """Validate the production-scale benchmark artifact contract."""

        if not _non_empty_text(artifact.get("environment")) or not _non_empty_text(artifact.get("generated_at")):
            return RuntimeBenchmarkArtifactValidation(valid=False, reason="MISSING_METADATA")

        raw_scenarios = artifact.get("scenarios")
        if not isinstance(raw_scenarios, Mapping):
            return RuntimeBenchmarkArtifactValidation(
                valid=False,
                reason="MISSING_SCENARIOS",
                missing_scenarios=tuple(sorted(scenario.name for scenario in self.scenarios)),
            )

        missing_scenarios = self.missing_required({str(name) for name in raw_scenarios})
        if missing_scenarios:
            return RuntimeBenchmarkArtifactValidation(
                valid=False,
                reason="MISSING_SCENARIOS",
                missing_scenarios=missing_scenarios,
            )

        missing_metrics: list[str] = []
        missing_thresholds: list[str] = []
        failed_thresholds: list[str] = []
        invalid_sample_counts: list[str] = []
        for scenario in self.scenarios:
            raw_result = raw_scenarios.get(scenario.name)
            result = raw_result if isinstance(raw_result, Mapping) else {}
            sample_count = result.get("sample_count")
            if not isinstance(sample_count, int) or sample_count <= 0:
                invalid_sample_counts.append(scenario.name)

            metrics = result.get("metrics")
            thresholds = result.get("thresholds")
            metrics_map = metrics if isinstance(metrics, Mapping) else {}
            thresholds_map = thresholds if isinstance(thresholds, Mapping) else {}
            for metric_name in sorted(scenario.required_metrics):
                metric_key = f"{scenario.name}.{metric_name}"
                if metric_name not in metrics_map:
                    missing_metrics.append(metric_key)
                    continue
                if metric_name not in thresholds_map:
                    missing_thresholds.append(metric_key)
                    continue
                metric_value = metrics_map[metric_name]
                threshold_value = thresholds_map[metric_name]
                if _is_number(metric_value) and _is_number(threshold_value) and metric_value > threshold_value:
                    failed_thresholds.append(metric_key)

        if invalid_sample_counts:
            return RuntimeBenchmarkArtifactValidation(
                valid=False,
                reason="INVALID_SAMPLE_COUNT",
                invalid_sample_counts=tuple(invalid_sample_counts),
            )
        if missing_metrics:
            return RuntimeBenchmarkArtifactValidation(
                valid=False,
                reason="MISSING_METRICS",
                missing_metrics=tuple(missing_metrics),
            )
        if missing_thresholds:
            return RuntimeBenchmarkArtifactValidation(
                valid=False,
                reason="MISSING_THRESHOLDS",
                missing_thresholds=tuple(missing_thresholds),
            )
        if failed_thresholds:
            return RuntimeBenchmarkArtifactValidation(
                valid=False,
                reason="THRESHOLD_EXCEEDED",
                failed_thresholds=tuple(failed_thresholds),
            )
        return RuntimeBenchmarkArtifactValidation(valid=True)


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


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
            required_metrics=frozenset({"write_p95_ms", "reconciling_count", "integrity_conflict_recheck_count"}),
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
    "RuntimeBenchmarkArtifactValidation",
    "RuntimeBenchmarkGate",
    "RuntimeBenchmarkScenario",
    "default_phase3_benchmark_scenarios",
    "runtime_benchmark_gate",
]
