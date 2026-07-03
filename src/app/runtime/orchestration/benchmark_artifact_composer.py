"""Compose Phase 3 production benchmark artifacts from scenario evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate, default_phase3_benchmark_scenarios


class RuntimeBenchmarkArtifactCompositionError(ValueError):
    """Raised when scenario evidence cannot produce a gate-valid artifact."""


class RuntimeBenchmarkArtifactComposer:
    """Build production-scale benchmark artifacts from per-scenario evidence files."""

    def __init__(self, gate: RuntimeBenchmarkGate | None = None) -> None:
        self._gate = gate or RuntimeBenchmarkGate()
        self._required_scenario_names = tuple(scenario.name for scenario in default_phase3_benchmark_scenarios())

    def compose_production_scale(
        self,
        *,
        environment: str,
        generated_at: str,
        dependency_profile: str,
        concurrency_level: int,
        duration_seconds: int,
        scenario_evidence_paths: Mapping[str, str | Path],
    ) -> dict[str, Any]:
        """Compose and validate a production-scale benchmark artifact."""

        self._reject_unknown_scenarios(scenario_evidence_paths)
        scenarios = {
            scenario_name: self._load_scenario_evidence(scenario_name, scenario_evidence_paths[scenario_name])
            for scenario_name in self._required_scenario_names
            if scenario_name in scenario_evidence_paths
        }
        artifact: dict[str, Any] = {
            "environment": environment,
            "generated_at": generated_at,
            "profile": {
                "kind": "production-scale",
                "database_backend": "postgresql",
                "dependency_profile": dependency_profile,
                "concurrency_level": concurrency_level,
                "duration_seconds": duration_seconds,
            },
            "scenarios": scenarios,
        }
        validation = self._gate.validate_artifact(artifact)
        if not validation.valid:
            raise RuntimeBenchmarkArtifactCompositionError(validation.reason)
        return artifact

    def _reject_unknown_scenarios(self, scenario_evidence_paths: Mapping[str, str | Path]) -> None:
        unknown_scenarios = sorted(set(scenario_evidence_paths) - set(self._required_scenario_names))
        if unknown_scenarios:
            raise RuntimeBenchmarkArtifactCompositionError(f"UNKNOWN_SCENARIOS: {', '.join(unknown_scenarios)}")

    def _load_scenario_evidence(self, scenario_name: str, raw_path: str | Path) -> dict[str, Any]:
        evidence_path = Path(raw_path)
        if not evidence_path.exists():
            raise RuntimeBenchmarkArtifactCompositionError(f"MISSING_EVIDENCE_FILE: {scenario_name}")

        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, Mapping):
            raise RuntimeBenchmarkArtifactCompositionError(f"INVALID_EVIDENCE_JSON: {scenario_name}")

        result = dict(evidence)
        source = result.get("source")
        result["source"] = dict(source) if isinstance(source, Mapping) else {}
        result["source"]["evidence"] = str(evidence_path)
        return result


runtime_benchmark_artifact_composer = RuntimeBenchmarkArtifactComposer()


__all__ = [
    "RuntimeBenchmarkArtifactComposer",
    "RuntimeBenchmarkArtifactCompositionError",
    "runtime_benchmark_artifact_composer",
]
