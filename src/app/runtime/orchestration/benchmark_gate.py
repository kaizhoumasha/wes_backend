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
    production_source_kinds: frozenset[str] = frozenset()
    blocks_phase_gate: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeBenchmarkArtifactValidation:
    """Validation result for a structured Phase 3 benchmark artifact."""

    valid: bool
    reason: str = "OK"
    missing_profile_fields: tuple[str, ...] = ()
    invalid_profile_fields: tuple[str, ...] = ()
    missing_scenarios: tuple[str, ...] = ()
    missing_metrics: tuple[str, ...] = ()
    missing_thresholds: tuple[str, ...] = ()
    failed_thresholds: tuple[str, ...] = ()
    invalid_sample_counts: tuple[str, ...] = ()
    missing_provenance_fields: tuple[str, ...] = ()
    invalid_provenance_fields: tuple[str, ...] = ()
    missing_workload_fields: tuple[str, ...] = ()
    invalid_workload_fields: tuple[str, ...] = ()


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

        profile = artifact.get("profile")
        profile_validation = _validate_profile_metadata(profile)
        if profile_validation is not None:
            return profile_validation

        scenario_result_validation = _validate_scenario_results(
            self.scenarios,
            raw_scenarios,
            require_production_provenance=_is_production_scale_profile(profile),
        )
        if scenario_result_validation is not None:
            return scenario_result_validation
        return RuntimeBenchmarkArtifactValidation(valid=True)


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


_REQUIRED_PROFILE_FIELDS = (
    "kind",
    "database_backend",
    "dependency_profile",
    "concurrency_level",
    "duration_seconds",
)
_ALLOWED_PROFILE_KINDS = frozenset({"lightweight", "production-scale"})


def _validate_profile_metadata(profile: object) -> RuntimeBenchmarkArtifactValidation | None:
    if not isinstance(profile, Mapping):
        return RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="MISSING_PROFILE_METADATA",
            missing_profile_fields=tuple(sorted(f"profile.{field}" for field in _REQUIRED_PROFILE_FIELDS)),
        )

    missing_fields = [
        f"profile.{field}" for field in _REQUIRED_PROFILE_FIELDS if field not in profile or profile[field] is None
    ]
    if missing_fields:
        return RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="MISSING_PROFILE_METADATA",
            missing_profile_fields=tuple(sorted(missing_fields)),
        )

    if profile["kind"] not in _ALLOWED_PROFILE_KINDS:
        return RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="INVALID_PROFILE_METADATA",
            invalid_profile_fields=("profile.kind",),
        )

    if profile["kind"] != "production-scale":
        return None

    invalid_fields: list[str] = []
    if profile["database_backend"] != "postgresql":
        invalid_fields.append("profile.database_backend")
    if not _non_empty_text(profile["dependency_profile"]):
        invalid_fields.append("profile.dependency_profile")
    if not _is_number(profile["concurrency_level"]) or profile["concurrency_level"] < 2:
        invalid_fields.append("profile.concurrency_level")
    if not _is_number(profile["duration_seconds"]) or profile["duration_seconds"] <= 0:
        invalid_fields.append("profile.duration_seconds")
    if invalid_fields:
        return RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="INVALID_PROFILE_METADATA",
            invalid_profile_fields=tuple(sorted(invalid_fields)),
        )
    return None


def _is_production_scale_profile(profile: object) -> bool:
    return isinstance(profile, Mapping) and profile.get("kind") == "production-scale"


def _validate_scenario_results(
    scenarios: list[RuntimeBenchmarkScenario],
    raw_scenarios: Mapping[object, object],
    *,
    require_production_provenance: bool = False,
) -> RuntimeBenchmarkArtifactValidation | None:
    missing_metrics: list[str] = []
    missing_thresholds: list[str] = []
    failed_thresholds: list[str] = []
    invalid_sample_counts: list[str] = []
    missing_provenance_fields: list[str] = []
    invalid_provenance_fields: list[str] = []
    missing_workload_fields: list[str] = []
    invalid_workload_fields: list[str] = []
    for scenario in scenarios:
        raw_result = raw_scenarios.get(scenario.name)
        result = raw_result if isinstance(raw_result, Mapping) else {}
        sample_count = result.get("sample_count")
        if not isinstance(sample_count, int) or sample_count <= 0:
            invalid_sample_counts.append(scenario.name)

        if require_production_provenance:
            _collect_scenario_provenance_validation(
                scenario,
                result,
                missing_fields=missing_provenance_fields,
                invalid_fields=invalid_provenance_fields,
            )
            _collect_scenario_workload_validation(
                scenario.name,
                result,
                missing_fields=missing_workload_fields,
                invalid_fields=invalid_workload_fields,
            )

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

    return _validation_from_collected_scenario_errors(
        invalid_sample_counts=invalid_sample_counts,
        missing_provenance_fields=missing_provenance_fields,
        invalid_provenance_fields=invalid_provenance_fields,
        missing_workload_fields=missing_workload_fields,
        invalid_workload_fields=invalid_workload_fields,
        missing_metrics=missing_metrics,
        missing_thresholds=missing_thresholds,
        failed_thresholds=failed_thresholds,
    )


def _validation_from_collected_scenario_errors(
    *,
    invalid_sample_counts: list[str],
    missing_provenance_fields: list[str],
    invalid_provenance_fields: list[str],
    missing_workload_fields: list[str],
    invalid_workload_fields: list[str],
    missing_metrics: list[str],
    missing_thresholds: list[str],
    failed_thresholds: list[str],
) -> RuntimeBenchmarkArtifactValidation | None:
    validation: RuntimeBenchmarkArtifactValidation | None = None
    if invalid_sample_counts:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="INVALID_SAMPLE_COUNT",
            invalid_sample_counts=tuple(invalid_sample_counts),
        )
    elif missing_provenance_fields:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="MISSING_SCENARIO_PROVENANCE",
            missing_provenance_fields=tuple(sorted(missing_provenance_fields)),
        )
    elif invalid_provenance_fields:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="INVALID_SCENARIO_PROVENANCE",
            invalid_provenance_fields=tuple(sorted(invalid_provenance_fields)),
        )
    elif missing_workload_fields:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="MISSING_WORKLOAD_METADATA",
            missing_workload_fields=tuple(sorted(missing_workload_fields)),
        )
    elif invalid_workload_fields:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="INVALID_WORKLOAD_METADATA",
            invalid_workload_fields=tuple(sorted(invalid_workload_fields)),
        )
    elif missing_metrics:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="MISSING_METRICS",
            missing_metrics=tuple(missing_metrics),
        )
    elif missing_thresholds:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="MISSING_THRESHOLDS",
            missing_thresholds=tuple(missing_thresholds),
        )
    elif failed_thresholds:
        validation = RuntimeBenchmarkArtifactValidation(
            valid=False,
            reason="THRESHOLD_EXCEEDED",
            failed_thresholds=tuple(failed_thresholds),
        )
    return validation


def _collect_scenario_provenance_validation(
    scenario: RuntimeBenchmarkScenario,
    result: Mapping[object, object],
    *,
    missing_fields: list[str],
    invalid_fields: list[str],
) -> None:
    source = result.get("source")
    if not isinstance(source, Mapping):
        missing_fields.append(f"{scenario.name}.source")
        return

    source_kind = source.get("kind")
    if not _non_empty_text(source_kind):
        missing_fields.append(f"{scenario.name}.source.kind")
    elif scenario.production_source_kinds and source_kind not in scenario.production_source_kinds:
        invalid_fields.append(f"{scenario.name}.source.kind")

    if not _non_empty_text(source.get("evidence")):
        missing_fields.append(f"{scenario.name}.source.evidence")


_PRODUCTION_WORKLOAD_REQUIREMENTS: dict[str, dict[str, int | bool]] = {
    "runtime_inbox_claim": {"pending_inbox_count": 1000, "worker_concurrency": 4},
    "conveyor_queue_writer": {"active_membership_count": 200, "concurrent_identity_collision": True},
    "ecs_status_command": {"status_get_count": 1, "command_post_count": 1},
    "plane_snapshot": {
        "workline_count": 1,
        "queue_count": 10,
        "device_count": 50,
        "active_session_count": 100,
        "active_object_count": 200,
    },
}


def _collect_scenario_workload_validation(
    scenario_name: str,
    result: Mapping[object, object],
    *,
    missing_fields: list[str],
    invalid_fields: list[str],
) -> None:
    requirements = _PRODUCTION_WORKLOAD_REQUIREMENTS.get(scenario_name, {})
    if not requirements:
        return

    workload = result.get("workload")
    if not isinstance(workload, Mapping):
        missing_fields.extend(f"{scenario_name}.workload.{field_name}" for field_name in requirements)
        return

    for field_name, expected_value in requirements.items():
        field_key = f"{scenario_name}.workload.{field_name}"
        actual_value = workload.get(field_name)
        if actual_value is None:
            missing_fields.append(field_key)
            continue
        if isinstance(expected_value, bool):
            if actual_value is not expected_value:
                invalid_fields.append(field_key)
            continue
        if not _is_number(actual_value) or actual_value < expected_value:
            invalid_fields.append(field_key)


def default_phase3_benchmark_scenarios() -> list[RuntimeBenchmarkScenario]:
    return [
        RuntimeBenchmarkScenario(
            name="runtime_inbox_claim",
            command="uv run pytest tests/load/test_runtime_inbox_claim_benchmark.py -q",
            required_metrics=frozenset({"claim_p95_ms", "duplicate_claim_count"}),
            production_source_kinds=frozenset({"postgresql"}),
        ),
        RuntimeBenchmarkScenario(
            name="conveyor_queue_writer",
            command="uv run pytest tests/load/test_conveyor_queue_writer_benchmark.py -q",
            required_metrics=frozenset({"write_p95_ms", "reconciling_count", "integrity_conflict_recheck_count"}),
            production_source_kinds=frozenset({"postgresql"}),
        ),
        RuntimeBenchmarkScenario(
            name="ecs_status_command",
            command="uv run pytest tests/load/test_ecs_status_command_benchmark.py -q",
            required_metrics=frozenset({"status_get_p95_ms", "command_post_p95_ms"}),
            production_source_kinds=frozenset({"ecs-http"}),
        ),
        RuntimeBenchmarkScenario(
            name="plane_snapshot",
            command="uv run pytest tests/load/test_plane_snapshot_benchmark.py -q",
            required_metrics=frozenset({"snapshot_p95_ms", "snapshot_10x_p95_ms"}),
            production_source_kinds=frozenset({"api-http"}),
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
