"""Phase 3 production P0 E2E artifact composer contract."""

from __future__ import annotations

import json
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
    assert artifact["source"] == {"kind": "trace-query", "evidence": str(recording_path)}
    assert artifact["exception_paths"]["ecs_timeout"] == {
        "result": "RECONCILING",
        "evidence": str(exception_paths["ecs_timeout"]),
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
