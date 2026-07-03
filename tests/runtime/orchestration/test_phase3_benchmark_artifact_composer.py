"""Phase 3 production benchmark artifact composer contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _scenario_evidence_payload(scenario_name: str) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {
        "runtime_inbox_claim": {
            "sample_count": 5000,
            "metrics": {"claim_p95_ms": 12.5, "duplicate_claim_count": 0},
            "thresholds": {"claim_p95_ms": 30.0, "duplicate_claim_count": 0},
            "source": {"kind": "postgresql"},
            "workload": {"pending_inbox_count": 1000, "worker_concurrency": 4},
        },
        "conveyor_queue_writer": {
            "sample_count": 5000,
            "metrics": {"write_p95_ms": 18.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 7},
            "thresholds": {"write_p95_ms": 30.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 25},
            "source": {"kind": "postgresql"},
            "workload": {"active_membership_count": 200, "concurrent_identity_collision": True},
        },
        "ecs_status_command": {
            "sample_count": 2000,
            "metrics": {"status_get_p95_ms": 20.0, "command_post_p95_ms": 24.0},
            "thresholds": {"status_get_p95_ms": 30.0, "command_post_p95_ms": 30.0},
            "source": {"kind": "ecs-http"},
            "workload": {"status_get_count": 400, "command_post_count": 400},
        },
        "plane_snapshot": {
            "sample_count": 2000,
            "metrics": {"snapshot_p95_ms": 21.0, "snapshot_10x_p95_ms": 70.0},
            "thresholds": {"snapshot_p95_ms": 30.0, "snapshot_10x_p95_ms": 100.0},
            "source": {"kind": "api-http"},
            "workload": {
                "workline_count": 1,
                "queue_count": 10,
                "device_count": 50,
                "active_session_count": 100,
                "active_object_count": 200,
            },
        },
    }
    return payloads[scenario_name]


def _write_evidence_files(tmp_path: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for scenario_name in (
        "runtime_inbox_claim",
        "conveyor_queue_writer",
        "ecs_status_command",
        "plane_snapshot",
    ):
        path = tmp_path / f"{scenario_name}.json"
        path.write_text(json.dumps(_scenario_evidence_payload(scenario_name), sort_keys=True), encoding="utf-8")
        paths[scenario_name] = path
    return paths


def test_phase3_benchmark_composer_builds_gate_valid_production_artifact(tmp_path) -> None:
    from src.app.runtime.orchestration.benchmark_artifact_composer import RuntimeBenchmarkArtifactComposer
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    evidence_paths = _write_evidence_files(tmp_path)

    artifact = RuntimeBenchmarkArtifactComposer().compose_production_scale(
        environment="field-benchmark",
        generated_at="2026-07-03T14:00:00Z",
        dependency_profile="postgresql-wms-ecs-http",
        concurrency_level=64,
        duration_seconds=300,
        scenario_evidence_paths=evidence_paths,
    )

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is True
    assert artifact["profile"]["kind"] == "production-scale"
    assert artifact["profile"]["database_backend"] == "postgresql"
    assert artifact["scenarios"]["runtime_inbox_claim"]["source"] == {
        "kind": "postgresql",
        "evidence": str(evidence_paths["runtime_inbox_claim"]),
    }


def test_phase3_benchmark_composer_rejects_missing_required_scenario_evidence(tmp_path) -> None:
    from src.app.runtime.orchestration.benchmark_artifact_composer import (
        RuntimeBenchmarkArtifactComposer,
        RuntimeBenchmarkArtifactCompositionError,
    )

    evidence_paths = _write_evidence_files(tmp_path)
    evidence_paths.pop("plane_snapshot")

    with pytest.raises(RuntimeBenchmarkArtifactCompositionError, match="MISSING_SCENARIOS"):
        RuntimeBenchmarkArtifactComposer().compose_production_scale(
            environment="field-benchmark",
            generated_at="2026-07-03T14:00:00Z",
            dependency_profile="postgresql-wms-ecs-http",
            concurrency_level=64,
            duration_seconds=300,
            scenario_evidence_paths=evidence_paths,
        )


def test_phase3_benchmark_composer_cli_writes_gate_valid_production_artifact(tmp_path) -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    evidence_paths = _write_evidence_files(tmp_path)
    output_path = tmp_path / "phase3-production-benchmark.json"
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/compose_phase3_runtime_benchmark_artifact.py",
            "--output",
            str(output_path),
            "--environment",
            "field-benchmark",
            "--generated-at",
            "2026-07-03T14:00:00Z",
            "--dependency-profile",
            "postgresql-wms-ecs-http",
            "--concurrency-level",
            "64",
            "--duration-seconds",
            "300",
            "--scenario-evidence",
            f"runtime_inbox_claim={evidence_paths['runtime_inbox_claim']}",
            "--scenario-evidence",
            f"conveyor_queue_writer={evidence_paths['conveyor_queue_writer']}",
            "--scenario-evidence",
            f"ecs_status_command={evidence_paths['ecs_status_command']}",
            "--scenario-evidence",
            f"plane_snapshot={evidence_paths['plane_snapshot']}",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert "Phase 3 production benchmark artifact written" in result.stdout
    assert RuntimeBenchmarkGate().validate_artifact(artifact).valid is True
