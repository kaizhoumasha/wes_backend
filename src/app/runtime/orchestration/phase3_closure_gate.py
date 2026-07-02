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
        for artifact_name in self._REQUIRED_ARTIFACTS:
            artifact = self._load_artifact(Path(artifact_paths[artifact_name]))
            if artifact is None:
                invalid_artifacts.append(f"{artifact_name}:INVALID_JSON")
                continue

            validation_reason = self._validate_named_artifact(artifact_name, artifact)
            if validation_reason is not None:
                invalid_artifacts.append(f"{artifact_name}:{validation_reason}")

        if invalid_artifacts:
            return RuntimePhase3ClosureValidation(
                valid=False,
                reason="INVALID_PHASE3_CLOSURE_ARTIFACTS",
                invalid_artifacts=tuple(invalid_artifacts),
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


runtime_phase3_closure_gate = RuntimePhase3ClosureGate()


__all__ = [
    "RuntimePhase3ClosureGate",
    "RuntimePhase3ClosureValidation",
    "runtime_phase3_closure_gate",
]
