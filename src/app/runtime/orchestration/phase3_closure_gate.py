"""Phase 3 closure evidence gate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate
from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate


@dataclass(frozen=True, slots=True)
class RuntimePhase3ClosureValidation:
    """Validation result for Phase 3 closure evidence artifacts."""

    valid: bool
    reason: str = "OK"
    missing_artifacts: tuple[str, ...] = ()
    invalid_artifacts: tuple[str, ...] = ()
    missing_evidence_files: tuple[str, ...] = ()


class RuntimePhase3ClosureGate:
    """Require the production P0 E2E and production benchmark artifacts before Phase 3 closure."""

    _REQUIRED_ARTIFACTS: ClassVar[tuple[str, ...]] = ("p0_e2e", "benchmark")
    _FORBIDDEN_BENCHMARK_ENVIRONMENTS: ClassVar[frozenset[str]] = frozenset(
        {"sandbox", "local-lightweight", "ci-lightweight", "lightweight"}
    )

    def __init__(
        self,
        *,
        p0_e2e_gate: RuntimeP0E2EGate | None = None,
        benchmark_gate: RuntimeBenchmarkGate | None = None,
    ) -> None:
        self._p0_e2e_gate = p0_e2e_gate or RuntimeP0E2EGate()
        self._benchmark_gate = benchmark_gate or RuntimeBenchmarkGate()

    def validate_artifact_files(
        self,
        artifact_paths: Mapping[str, str | Path],
    ) -> RuntimePhase3ClosureValidation:
        """Validate the complete Phase 3 closure evidence set from artifact JSON files."""

        missing_artifacts = tuple(
            artifact_name
            for artifact_name in self._REQUIRED_ARTIFACTS
            if artifact_name not in artifact_paths or not Path(artifact_paths[artifact_name]).exists()
        )
        if missing_artifacts:
            return RuntimePhase3ClosureValidation(
                valid=False,
                reason="MISSING_PHASE3_CLOSURE_ARTIFACTS",
                missing_artifacts=missing_artifacts,
            )

        unknown_artifacts = tuple(sorted(set(artifact_paths) - set(self._REQUIRED_ARTIFACTS)))
        if unknown_artifacts:
            return RuntimePhase3ClosureValidation(
                valid=False,
                reason="UNKNOWN_PHASE3_CLOSURE_ARTIFACTS",
                invalid_artifacts=unknown_artifacts,
            )

        invalid_artifacts: list[str] = []
        loaded_artifacts: dict[str, tuple[Path, dict[str, Any]]] = {}
        for artifact_name in self._REQUIRED_ARTIFACTS:
            artifact_path = Path(artifact_paths[artifact_name])
            artifact = self._load_artifact(artifact_path)
            if artifact is None:
                invalid_artifacts.append(f"{artifact_name}:INVALID_JSON")
                continue

            validation_reason = self._validate_named_artifact(artifact_name, artifact)
            if validation_reason is not None:
                invalid_artifacts.append(f"{artifact_name}:{validation_reason}")
                continue
            loaded_artifacts[artifact_name] = (artifact_path, artifact)

        if invalid_artifacts:
            return RuntimePhase3ClosureValidation(
                valid=False,
                reason="INVALID_PHASE3_CLOSURE_ARTIFACTS",
                invalid_artifacts=tuple(invalid_artifacts),
            )

        missing_evidence_files = tuple(
            sorted(
                missing_file
                for artifact_name, (artifact_path, artifact) in loaded_artifacts.items()
                for missing_file in _missing_referenced_evidence_files(
                    artifact_name=artifact_name,
                    artifact_path=artifact_path,
                    artifact=artifact,
                )
            )
        )
        if missing_evidence_files:
            return RuntimePhase3ClosureValidation(
                valid=False,
                reason="MISSING_PHASE3_CLOSURE_EVIDENCE_FILES",
                missing_evidence_files=missing_evidence_files,
            )

        return RuntimePhase3ClosureValidation(valid=True)

    def _load_artifact(self, artifact_path: Path) -> dict[str, Any] | None:
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return dict(artifact) if isinstance(artifact, Mapping) else None

    def _validate_named_artifact(self, artifact_name: str, artifact: Mapping[str, object]) -> str | None:
        if artifact_name == "p0_e2e":
            validation = self._p0_e2e_gate.validate_artifact(artifact)
            return None if validation.valid else validation.reason
        if artifact_name == "benchmark":
            profile = artifact.get("profile")
            if not isinstance(profile, Mapping) or profile.get("kind") != "production-scale":
                return "LIGHTWEIGHT_BENCHMARK_NOT_ALLOWED"
            environment = artifact.get("environment")
            if isinstance(environment, str) and environment.strip().lower() in self._FORBIDDEN_BENCHMARK_ENVIRONMENTS:
                return "NON_PRODUCTION_BENCHMARK_ENVIRONMENT"
            validation = self._benchmark_gate.validate_artifact(artifact)
            return None if validation.valid else validation.reason
        return "UNKNOWN"


def _missing_referenced_evidence_files(
    *,
    artifact_name: str,
    artifact_path: Path,
    artifact: Mapping[str, object],
) -> tuple[str, ...]:
    evidence_fields = _referenced_evidence_fields(artifact_name, artifact)
    return tuple(
        f"{artifact_name}:{field_name}"
        for field_name, raw_path in evidence_fields.items()
        if not _evidence_file_exists(base_dir=artifact_path.parent, raw_path=raw_path)
    )


def _referenced_evidence_fields(artifact_name: str, artifact: Mapping[str, object]) -> dict[str, object]:
    if artifact_name == "p0_e2e":
        return _p0_e2e_evidence_fields(artifact)
    if artifact_name == "benchmark":
        return _benchmark_evidence_fields(artifact)
    return {}


def _p0_e2e_evidence_fields(artifact: Mapping[str, object]) -> dict[str, object]:
    evidence_fields: dict[str, object] = {}
    source = artifact.get("source")
    if isinstance(source, Mapping):
        evidence_fields["source.evidence"] = source.get("evidence")

    exception_paths = artifact.get("exception_paths")
    if isinstance(exception_paths, Mapping):
        for path_name, raw_exception_path in exception_paths.items():
            if isinstance(path_name, str) and isinstance(raw_exception_path, Mapping):
                evidence_fields[f"exception_paths.{path_name}.evidence"] = raw_exception_path.get("evidence")
    return evidence_fields


def _benchmark_evidence_fields(artifact: Mapping[str, object]) -> dict[str, object]:
    scenarios = artifact.get("scenarios")
    if not isinstance(scenarios, Mapping):
        return {}

    evidence_fields: dict[str, object] = {}
    for scenario_name, raw_scenario in scenarios.items():
        if not isinstance(scenario_name, str) or not isinstance(raw_scenario, Mapping):
            continue
        source = raw_scenario.get("source")
        if isinstance(source, Mapping):
            evidence_fields[f"scenarios.{scenario_name}.source.evidence"] = source.get("evidence")
    return evidence_fields


def _evidence_file_exists(*, base_dir: Path, raw_path: object) -> bool:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    evidence_path = Path(raw_path)
    if not evidence_path.is_absolute():
        evidence_path = base_dir / evidence_path
    return evidence_path.is_file()


runtime_phase3_closure_gate = RuntimePhase3ClosureGate()


__all__ = [
    "RuntimePhase3ClosureGate",
    "RuntimePhase3ClosureValidation",
    "runtime_phase3_closure_gate",
]
