"""Compose runtime production P0 E2E artifacts from trace evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate


class RuntimeP0E2EArtifactCompositionError(ValueError):
    """Raised when trace evidence cannot produce a gate-valid P0 E2E artifact."""


class RuntimeP0E2EArtifactComposer:
    """Build production P0 E2E artifacts from trace recording and exception evidence files."""

    _REQUIRED_EXCEPTION_PATHS: ClassVar[tuple[str, ...]] = ("callback_out_of_order", "ecs_timeout", "wms_reject")

    def __init__(self, gate: RuntimeP0E2EGate | None = None) -> None:
        self._gate = gate or RuntimeP0E2EGate()

    def compose_production_e2e(
        self,
        *,
        environment: str,
        dependency_profile: str,
        trace_recording_path: str | Path,
        p95_seconds: float,
        exception_evidence_paths: Mapping[str, str | Path],
    ) -> dict[str, Any]:
        """Compose and validate a production P0 E2E artifact."""

        recording_path = Path(trace_recording_path)
        recording, recording_path, recording_hash = self._load_trace_recording(recording_path)
        artifact: dict[str, Any] = {
            "profile": {
                "kind": "production-e2e",
                "environment": environment,
                "dependency_profile": dependency_profile,
            },
            "source": {
                "kind": "trace-query",
                "environment": environment,
                "evidence": str(recording_path),
                "evidence_sha256": recording_hash,
            },
            "latency": {"p95_seconds": p95_seconds},
            "recording": recording,
            "exception_paths": self._compose_exception_paths(exception_evidence_paths),
        }
        validation = self._gate.validate_artifact(artifact)
        if not validation.valid:
            raise RuntimeP0E2EArtifactCompositionError(validation.reason)
        return artifact

    def _load_trace_recording(self, recording_path: Path) -> tuple[dict[str, Any], Path, str]:
        if not recording_path.is_file():
            raise RuntimeP0E2EArtifactCompositionError("MISSING_TRACE_RECORDING")
        recording_path = recording_path.resolve()
        recording_bytes = recording_path.read_bytes()
        recording = json.loads(recording_bytes.decode("utf-8"))
        if not isinstance(recording, Mapping):
            raise RuntimeP0E2EArtifactCompositionError("INVALID_TRACE_RECORDING")
        return dict(recording), recording_path, hashlib.sha256(recording_bytes).hexdigest()

    def _compose_exception_paths(self, raw_exception_paths: Mapping[str, str | Path]) -> dict[str, dict[str, str]]:
        unknown_paths = sorted(set(raw_exception_paths) - set(self._REQUIRED_EXCEPTION_PATHS))
        if unknown_paths:
            raise RuntimeP0E2EArtifactCompositionError(f"UNKNOWN_EXCEPTION_PATHS: {', '.join(unknown_paths)}")

        missing_paths = tuple(
            path_name for path_name in self._REQUIRED_EXCEPTION_PATHS if path_name not in raw_exception_paths
        )
        if missing_paths:
            raise RuntimeP0E2EArtifactCompositionError(f"MISSING_EXCEPTION_PATHS: {', '.join(missing_paths)}")

        exception_paths: dict[str, dict[str, str]] = {}
        seen_evidence_paths: set[Path] = set()
        for path_name in self._REQUIRED_EXCEPTION_PATHS:
            evidence_path = Path(raw_exception_paths[path_name])
            if not evidence_path.is_file():
                raise RuntimeP0E2EArtifactCompositionError(f"MISSING_EXCEPTION_EVIDENCE: {path_name}")
            evidence_path = evidence_path.resolve()
            if evidence_path in seen_evidence_paths:
                raise RuntimeP0E2EArtifactCompositionError(f"DUPLICATE_EXCEPTION_EVIDENCE_FILE: {path_name}")
            seen_evidence_paths.add(evidence_path)
            evidence_bytes = evidence_path.read_bytes()
            evidence = json.loads(evidence_bytes.decode("utf-8"))
            if not isinstance(evidence, Mapping):
                raise RuntimeP0E2EArtifactCompositionError(f"INVALID_EXCEPTION_EVIDENCE: {path_name}")
            result = evidence.get("result")
            if result != "RECONCILING":
                raise RuntimeP0E2EArtifactCompositionError(f"INVALID_EXCEPTION_PATHS: {path_name}.result")
            if evidence.get("case") != path_name:
                raise RuntimeP0E2EArtifactCompositionError(f"INVALID_EXCEPTION_PATHS: {path_name}.case")
            exception_paths[path_name] = {
                "result": result,
                "evidence": str(evidence_path),
                "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            }
        return exception_paths


runtime_p0_e2e_artifact_composer = RuntimeP0E2EArtifactComposer()


__all__ = [
    "RuntimeP0E2EArtifactComposer",
    "RuntimeP0E2EArtifactCompositionError",
    "runtime_p0_e2e_artifact_composer",
]
