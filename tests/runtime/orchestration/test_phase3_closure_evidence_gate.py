"""Phase 3 closure evidence gate contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _p0_e2e_artifact() -> dict[str, Any]:
    return {
        "profile": {
            "kind": "production-e2e",
            "environment": "field-dry-run",
            "dependency_profile": "wms-ecs-http",
        },
        "source": {"kind": "trace-query", "evidence": "trace-query://phase3/p0-e2e"},
        "latency": {"p95_seconds": 18.7},
        "recording": {
            "events": [
                {"kind": "workline_manifest", "payload": {"object_key": "workline:WL-PROD"}},
                {"kind": "execution_session", "payload": {"object_key": "session:S-PROD"}},
                {"kind": "runtime_inbox", "payload": {"source_event_id": "ecs-scan-prod-1"}},
                {
                    "kind": "runtime_intent",
                    "payload": {"effect_key": "device-command:CMD-PROD-1"},
                },
                {
                    "kind": "device_command",
                    "payload": {"effect_key": "device-command:CMD-PROD-1"},
                },
                {
                    "kind": "wms_fulfillment",
                    "payload": {"effect_key": "wms-fulfillment:FUL-PROD-1"},
                },
                {"kind": "plane_snapshot", "payload": {"object_key": "pkg:PKG-PROD-0001"}},
            ],
        },
        "exception_paths": {
            "callback_out_of_order": {"result": "RECONCILING", "evidence": "trace-query://phase3/callback"},
            "ecs_timeout": {"result": "RECONCILING", "evidence": "trace-query://phase3/ecs-timeout"},
            "wms_reject": {"result": "RECONCILING", "evidence": "trace-query://phase3/wms-reject"},
        },
    }


def _benchmark_artifact() -> dict[str, Any]:
    return {
        "environment": "field-benchmark",
        "generated_at": "2026-07-03T14:00:00Z",
        "profile": {
            "kind": "production-scale",
            "database_backend": "postgresql",
            "dependency_profile": "postgresql-wms-ecs-http",
            "concurrency_level": 64,
            "duration_seconds": 300,
        },
        "scenarios": {
            "runtime_inbox_claim": {
                "sample_count": 5000,
                "metrics": {"claim_p95_ms": 12.5, "duplicate_claim_count": 0},
                "thresholds": {"claim_p95_ms": 30.0, "duplicate_claim_count": 0},
                "source": {"kind": "postgresql", "evidence": "postgresql://phase3/runtime-inbox-claim"},
                "workload": {"pending_inbox_count": 1000, "worker_concurrency": 4},
            },
            "conveyor_queue_writer": {
                "sample_count": 5000,
                "metrics": {"write_p95_ms": 18.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 7},
                "thresholds": {"write_p95_ms": 30.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 25},
                "source": {"kind": "postgresql", "evidence": "postgresql://phase3/queue-writer"},
                "workload": {"active_membership_count": 200, "concurrent_identity_collision": True},
            },
            "ecs_status_command": {
                "sample_count": 2000,
                "metrics": {"status_get_p95_ms": 20.0, "command_post_p95_ms": 24.0},
                "thresholds": {"status_get_p95_ms": 30.0, "command_post_p95_ms": 30.0},
                "source": {"kind": "ecs-http", "evidence": "https://ecs.example.invalid/phase3"},
                "workload": {"status_get_count": 400, "command_post_count": 400},
            },
            "plane_snapshot": {
                "sample_count": 2000,
                "metrics": {"snapshot_p95_ms": 21.0, "snapshot_10x_p95_ms": 70.0},
                "thresholds": {"snapshot_p95_ms": 30.0, "snapshot_10x_p95_ms": 100.0},
                "source": {"kind": "api-http", "evidence": "https://wes.example.invalid/plane-snapshot"},
                "workload": {
                    "workline_count": 1,
                    "queue_count": 10,
                    "device_count": 50,
                    "active_session_count": 100,
                    "active_object_count": 200,
                },
            },
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_evidence_file(base_dir: Path, relative_path: str, payload: dict[str, Any]) -> str:
    evidence_path = base_dir / relative_path
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return relative_path


def _write_closure_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    p0_e2e_path = _write_json(tmp_path / "phase3-p0-e2e.json", _p0_e2e_artifact())
    benchmark_path = _write_json(tmp_path / "phase3-production-benchmark.json", _benchmark_artifact())
    return p0_e2e_path, benchmark_path


def _write_closure_artifacts_with_evidence(tmp_path: Path) -> tuple[Path, Path]:
    p0_e2e_artifact = _p0_e2e_artifact()
    p0_e2e_artifact["source"]["evidence"] = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/source.json",
        p0_e2e_artifact["recording"],
    )
    p0_e2e_artifact["exception_paths"]["callback_out_of_order"]["evidence"] = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/callback-out-of-order.json",
        {"case": "callback_out_of_order", "result": "RECONCILING"},
    )
    p0_e2e_artifact["exception_paths"]["ecs_timeout"]["evidence"] = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/ecs-timeout.json",
        {"case": "ecs_timeout", "result": "RECONCILING"},
    )
    p0_e2e_artifact["exception_paths"]["wms_reject"]["evidence"] = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/wms-reject.json",
        {"case": "wms_reject", "result": "RECONCILING"},
    )

    benchmark_artifact = _benchmark_artifact()
    for scenario_name in benchmark_artifact["scenarios"]:
        benchmark_artifact["scenarios"][scenario_name]["source"]["evidence"] = _write_evidence_file(
            tmp_path,
            f"evidence/benchmark/{scenario_name}.json",
            benchmark_artifact["scenarios"][scenario_name],
        )

    p0_e2e_path = _write_json(tmp_path / "phase3-p0-e2e.json", p0_e2e_artifact)
    benchmark_path = _write_json(tmp_path / "phase3-production-benchmark.json", benchmark_artifact)
    return p0_e2e_path, benchmark_path


def test_phase3_closure_gate_requires_both_production_artifacts(tmp_path) -> None:
    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    p0_e2e_path = _write_json(tmp_path / "phase3-p0-e2e.json", _p0_e2e_artifact())

    validation = RuntimePhase3ClosureGate().validate_artifact_files({"p0_e2e": p0_e2e_path})

    assert validation.valid is False
    assert validation.reason == "MISSING_PHASE3_CLOSURE_ARTIFACTS"
    assert validation.missing_artifacts == ("benchmark",)


def test_phase3_closure_gate_accepts_valid_p0_and_benchmark_artifacts(tmp_path) -> None:
    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path}
    )

    assert validation.valid is True
    assert validation.reason == "OK"


def test_phase3_closure_gate_rejects_missing_referenced_evidence_files(tmp_path) -> None:
    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts(tmp_path)

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path}
    )

    assert validation.valid is False
    assert validation.reason == "MISSING_PHASE3_CLOSURE_EVIDENCE_FILES"
    assert validation.missing_evidence_files == (
        "benchmark:scenarios.conveyor_queue_writer.source.evidence",
        "benchmark:scenarios.ecs_status_command.source.evidence",
        "benchmark:scenarios.plane_snapshot.source.evidence",
        "benchmark:scenarios.runtime_inbox_claim.source.evidence",
        "p0_e2e:exception_paths.callback_out_of_order.evidence",
        "p0_e2e:exception_paths.ecs_timeout.evidence",
        "p0_e2e:exception_paths.wms_reject.evidence",
        "p0_e2e:source.evidence",
    )


def test_phase3_closure_gate_rejects_mismatched_referenced_evidence_files(tmp_path) -> None:
    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)
    (tmp_path / "evidence/p0-e2e/source.json").write_text(
        json.dumps({"events": []}, sort_keys=True),
        encoding="utf-8",
    )

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path}
    )

    assert validation.valid is False
    assert validation.reason == "MISMATCHED_PHASE3_CLOSURE_EVIDENCE_FILES"
    assert validation.mismatched_evidence_files == ("p0_e2e:source.evidence",)


def test_phase3_closure_gate_rejects_lightweight_benchmark_artifact(tmp_path) -> None:
    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    p0_e2e_path = _write_json(tmp_path / "phase3-p0-e2e.json", _p0_e2e_artifact())
    benchmark_artifact = _benchmark_artifact()
    benchmark_artifact["environment"] = "local-lightweight"
    benchmark_artifact["profile"] = {
        "kind": "lightweight",
        "database_backend": "in-memory",
        "dependency_profile": "in-process-contract",
        "concurrency_level": 1,
        "duration_seconds": 0,
    }
    benchmark_path = _write_json(tmp_path / "phase3-lightweight-benchmark.json", benchmark_artifact)

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path}
    )

    assert validation.valid is False
    assert validation.reason == "INVALID_PHASE3_CLOSURE_ARTIFACTS"
    assert validation.invalid_artifacts == ("benchmark:LIGHTWEIGHT_BENCHMARK_NOT_ALLOWED",)


def test_phase3_closure_gate_rejects_non_production_benchmark_environment(tmp_path) -> None:
    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    p0_e2e_path = _write_json(tmp_path / "phase3-p0-e2e.json", _p0_e2e_artifact())
    benchmark_artifact = _benchmark_artifact()
    benchmark_artifact["environment"] = "local-lightweight"
    benchmark_path = _write_json(tmp_path / "phase3-local-benchmark.json", benchmark_artifact)

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path}
    )

    assert validation.valid is False
    assert validation.reason == "INVALID_PHASE3_CLOSURE_ARTIFACTS"
    assert validation.invalid_artifacts == ("benchmark:NON_PRODUCTION_BENCHMARK_ENVIRONMENT",)


def test_phase3_closure_gate_cli_validates_artifact_set(tmp_path) -> None:
    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_phase3_closure_gate.py",
            "--p0-e2e-artifact",
            str(p0_e2e_path),
            "--benchmark-artifact",
            str(benchmark_path),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Phase 3 closure evidence passed" in result.stdout
