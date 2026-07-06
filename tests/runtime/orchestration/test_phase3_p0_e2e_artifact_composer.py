"""Phase 3 production P0 E2E artifact composer contract."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _production_recording() -> dict[str, Any]:
    return {
        "scenario_id": "phase3-p0-production-e2e",
        "schema_version": "scenario.v1",
        "events": [
            {
                "event_id": "manifest-001",
                "kind": "workline_manifest",
                "occurred_at": "2026-07-03T10:00:00Z",
                "payload": {"object_key": "workline:WL-PROD", "state": "ACTIVE"},
            },
            {
                "event_id": "session-001",
                "kind": "execution_session",
                "occurred_at": "2026-07-03T10:00:01Z",
                "payload": {"object_key": "session:S-PROD", "state": "RUNNING"},
            },
            {
                "event_id": "inbox-001",
                "kind": "runtime_inbox",
                "occurred_at": "2026-07-03T10:00:02Z",
                "payload": {"source_event_id": "ecs-scan-prod-1", "object_key": "pkg:PKG-PROD-0001"},
            },
            {
                "event_id": "intent-001",
                "kind": "runtime_intent",
                "occurred_at": "2026-07-03T10:00:03Z",
                "payload": {"effect_key": "device-command:CMD-PROD-1", "object_key": "pkg:PKG-PROD-0001"},
            },
            {
                "event_id": "device-001",
                "kind": "device_command",
                "occurred_at": "2026-07-03T10:00:04Z",
                "payload": {"effect_key": "device-command:CMD-PROD-1", "state": "ACKED"},
            },
            {
                "event_id": "wms-001",
                "kind": "wms_fulfillment",
                "occurred_at": "2026-07-03T10:00:05Z",
                "payload": {"effect_key": "wms-fulfillment:FUL-PROD-1", "state": "SUCCEEDED"},
            },
            {
                "event_id": "plane-001",
                "kind": "plane_snapshot",
                "occurred_at": "2026-07-03T10:00:06Z",
                "payload": {"object_key": "pkg:PKG-PROD-0001", "state": "VISIBLE"},
            },
        ],
    }


def _write_production_e2e_inputs(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    recording_path = tmp_path / "trace-recording.json"
    recording_path.write_text(json.dumps(_production_recording(), sort_keys=True), encoding="utf-8")
    exception_paths: dict[str, Path] = {}
    for path_name in ("callback_out_of_order", "ecs_timeout", "wms_reject"):
        evidence_path = tmp_path / f"{path_name}.json"
        evidence_path.write_text(
            json.dumps({"result": "RECONCILING", "case": path_name}, sort_keys=True),
            encoding="utf-8",
        )
        exception_paths[path_name] = evidence_path
    return recording_path, exception_paths


def test_phase3_p0_e2e_composer_builds_gate_valid_production_artifact(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import RuntimeP0E2EArtifactComposer
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)

    artifact = RuntimeP0E2EArtifactComposer().compose_production_e2e(
        environment="field-dry-run",
        dependency_profile="wms-ecs-http",
        trace_recording_path=recording_path,
        p95_seconds=18.7,
        exception_evidence_paths=exception_paths,
    )

    validation = RuntimeP0E2EGate().validate_artifact(artifact)

    assert validation.valid is True
    assert artifact["source"] == {
        "kind": "trace-query",
        "environment": "field-dry-run",
        "evidence": str(recording_path),
        "evidence_sha256": hashlib.sha256(recording_path.read_bytes()).hexdigest(),
    }
    assert artifact["exception_paths"]["ecs_timeout"] == {
        "result": "RECONCILING",
        "evidence": str(exception_paths["ecs_timeout"]),
        "evidence_sha256": hashlib.sha256(exception_paths["ecs_timeout"].read_bytes()).hexdigest(),
    }


def test_phase3_p0_e2e_composer_rejects_missing_exception_evidence(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    exception_paths.pop("wms_reject")

    with pytest.raises(RuntimeP0E2EArtifactCompositionError, match="MISSING_EXCEPTION_PATHS"):
        RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment="field-dry-run",
            dependency_profile="wms-ecs-http",
            trace_recording_path=recording_path,
            p95_seconds=18.7,
            exception_evidence_paths=exception_paths,
        )


@pytest.mark.parametrize("environment", ["sandbox", "local-lightweight", "ci-lightweight"])
def test_phase3_p0_e2e_composer_rejects_non_production_environment(tmp_path, environment: str) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)

    with pytest.raises(RuntimeP0E2EArtifactCompositionError, match="INVALID_PROFILE_METADATA"):
        RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment=environment,
            dependency_profile="wms-ecs-http",
            trace_recording_path=recording_path,
            p95_seconds=18.7,
            exception_evidence_paths=exception_paths,
        )


def test_phase3_p0_e2e_composer_rejects_latency_at_threshold(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)

    with pytest.raises(RuntimeP0E2EArtifactCompositionError, match="E2E_LATENCY_EXCEEDED"):
        RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment="field-dry-run",
            dependency_profile="wms-ecs-http",
            trace_recording_path=recording_path,
            p95_seconds=30.0,
            exception_evidence_paths=exception_paths,
        )


def test_phase3_p0_e2e_composer_rejects_exception_evidence_without_reconciling_result(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    exception_paths["ecs_timeout"].write_text(
        json.dumps({"result": "COMPLETED", "case": "ecs_timeout"}, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeP0E2EArtifactCompositionError, match="INVALID_EXCEPTION_PATHS"):
        RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment="field-dry-run",
            dependency_profile="wms-ecs-http",
            trace_recording_path=recording_path,
            p95_seconds=18.7,
            exception_evidence_paths=exception_paths,
        )


def test_phase3_p0_e2e_composer_rejects_reused_exception_evidence_file(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    exception_paths["wms_reject"] = exception_paths["ecs_timeout"]

    with pytest.raises(RuntimeP0E2EArtifactCompositionError, match="DUPLICATE_EXCEPTION_EVIDENCE_FILE"):
        RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment="field-dry-run",
            dependency_profile="wms-ecs-http",
            trace_recording_path=recording_path,
            p95_seconds=18.7,
            exception_evidence_paths=exception_paths,
        )


def test_phase3_p0_e2e_composer_rejects_mismatched_exception_case(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    exception_paths["ecs_timeout"].write_text(
        json.dumps({"result": "RECONCILING", "case": "wms_reject"}, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeP0E2EArtifactCompositionError, match="INVALID_EXCEPTION_PATHS"):
        RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment="field-dry-run",
            dependency_profile="wms-ecs-http",
            trace_recording_path=recording_path,
            p95_seconds=18.7,
            exception_evidence_paths=exception_paths,
        )


def test_phase3_p0_e2e_composer_cli_rejects_duplicate_exception_evidence(tmp_path) -> None:
    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    output_path = tmp_path / "phase3-p0-e2e.json"
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/compose_phase3_p0_e2e_artifact.py",
            "--output",
            str(output_path),
            "--environment",
            "field-dry-run",
            "--dependency-profile",
            "wms-ecs-http",
            "--trace-recording",
            str(recording_path),
            "--p95-seconds",
            "18.7",
            "--exception-evidence",
            f"ecs_timeout={exception_paths['ecs_timeout']}",
            "--exception-evidence",
            f"ecs_timeout={exception_paths['ecs_timeout']}",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "DUPLICATE_EXCEPTION_EVIDENCE" in result.stdout


def test_phase3_p0_e2e_composer_cli_normalizes_relative_evidence_paths(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    output_path = tmp_path / "reports" / "phase3" / "phase3-p0-e2e.json"
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/compose_phase3_p0_e2e_artifact.py",
            "--output",
            str(output_path),
            "--environment",
            "field-dry-run",
            "--dependency-profile",
            "wms-ecs-http",
            "--trace-recording",
            os.path.relpath(recording_path, repo_root),
            "--p95-seconds",
            "18.7",
            "--exception-evidence",
            f"callback_out_of_order={os.path.relpath(exception_paths['callback_out_of_order'], repo_root)}",
            "--exception-evidence",
            f"ecs_timeout={os.path.relpath(exception_paths['ecs_timeout'], repo_root)}",
            "--exception-evidence",
            f"wms_reject={os.path.relpath(exception_paths['wms_reject'], repo_root)}",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert "Phase 3 P0 E2E artifact written" in result.stdout
    assert RuntimeP0E2EGate().validate_artifact(artifact).valid is True
    assert Path(artifact["source"]["evidence"]).is_absolute()
    for exception_path in artifact["exception_paths"].values():
        assert Path(exception_path["evidence"]).is_absolute()


def test_phase3_p0_e2e_composer_cli_writes_gate_valid_artifact(tmp_path) -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    recording_path, exception_paths = _write_production_e2e_inputs(tmp_path)
    output_path = tmp_path / "phase3-p0-e2e.json"
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/compose_phase3_p0_e2e_artifact.py",
            "--output",
            str(output_path),
            "--environment",
            "field-dry-run",
            "--dependency-profile",
            "wms-ecs-http",
            "--trace-recording",
            str(recording_path),
            "--p95-seconds",
            "18.7",
            "--exception-evidence",
            f"callback_out_of_order={exception_paths['callback_out_of_order']}",
            "--exception-evidence",
            f"ecs_timeout={exception_paths['ecs_timeout']}",
            "--exception-evidence",
            f"wms_reject={exception_paths['wms_reject']}",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert "Phase 3 P0 E2E artifact written" in result.stdout
    assert RuntimeP0E2EGate().validate_artifact(artifact).valid is True
