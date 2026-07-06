"""Phase 3 production benchmark artifact composer contract."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.load.phase3_benchmark_scenarios import production_scenario_metadata


def _scenario_evidence_payload(scenario_name: str) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {
        "runtime_inbox_claim": {
            "sample_count": 5000,
            "metrics": {"claim_p95_ms": 12.5, "duplicate_claim_count": 0},
            "thresholds": {"claim_p95_ms": 30.0, "duplicate_claim_count": 0},
        },
        "conveyor_queue_writer": {
            "sample_count": 5000,
            "metrics": {"write_p95_ms": 18.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 7},
            "thresholds": {"write_p95_ms": 30.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 25},
        },
        "ecs_status_command": {
            "sample_count": 2000,
            "metrics": {"status_get_p95_ms": 20.0, "command_post_p95_ms": 24.0},
            "thresholds": {"status_get_p95_ms": 30.0, "command_post_p95_ms": 30.0},
        },
        "plane_snapshot": {
            "sample_count": 2000,
            "metrics": {"snapshot_p95_ms": 21.0, "snapshot_10x_p95_ms": 70.0},
            "thresholds": {"snapshot_p95_ms": 30.0, "snapshot_10x_p95_ms": 100.0},
        },
    }
    return payloads[scenario_name] | production_scenario_metadata(scenario_name)


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
    expected_evidence_hash = hashlib.sha256(evidence_paths["runtime_inbox_claim"].read_bytes()).hexdigest()
    assert artifact["scenarios"]["runtime_inbox_claim"]["source"] == {
        "kind": "postgresql",
        "evidence": str(evidence_paths["runtime_inbox_claim"]),
        "evidence_sha256": expected_evidence_hash,
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


@pytest.mark.parametrize("environment", ["local-lightweight", "ci-lightweight", "sandbox"])
def test_phase3_benchmark_composer_rejects_non_production_environment(tmp_path, environment: str) -> None:
    from src.app.runtime.orchestration.benchmark_artifact_composer import (
        RuntimeBenchmarkArtifactComposer,
        RuntimeBenchmarkArtifactCompositionError,
    )

    evidence_paths = _write_evidence_files(tmp_path)

    with pytest.raises(RuntimeBenchmarkArtifactCompositionError, match="NON_PRODUCTION_BENCHMARK_ENVIRONMENT"):
        RuntimeBenchmarkArtifactComposer().compose_production_scale(
            environment=environment,
            generated_at="2026-07-03T14:00:00Z",
            dependency_profile="postgresql-wms-ecs-http",
            concurrency_level=64,
            duration_seconds=300,
            scenario_evidence_paths=evidence_paths,
        )


def test_phase3_benchmark_composer_cli_rejects_duplicate_scenario_evidence(tmp_path) -> None:
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
            f"runtime_inbox_claim={evidence_paths['runtime_inbox_claim']}",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "DUPLICATE_SCENARIO_EVIDENCE" in result.stdout


def test_phase3_benchmark_composer_cli_normalizes_relative_evidence_paths(tmp_path) -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    evidence_paths = _write_evidence_files(tmp_path)
    output_path = tmp_path / "reports" / "phase3" / "phase3-production-benchmark.json"
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
            f"runtime_inbox_claim={os.path.relpath(evidence_paths['runtime_inbox_claim'], repo_root)}",
            "--scenario-evidence",
            f"conveyor_queue_writer={os.path.relpath(evidence_paths['conveyor_queue_writer'], repo_root)}",
            "--scenario-evidence",
            f"ecs_status_command={os.path.relpath(evidence_paths['ecs_status_command'], repo_root)}",
            "--scenario-evidence",
            f"plane_snapshot={os.path.relpath(evidence_paths['plane_snapshot'], repo_root)}",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert "Phase 3 production benchmark artifact written" in result.stdout
    assert RuntimeBenchmarkGate().validate_artifact(artifact).valid is True
    for scenario in artifact["scenarios"].values():
        evidence_path = Path(scenario["source"]["evidence"])
        assert evidence_path.is_absolute()
        assert evidence_path.is_file()


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
