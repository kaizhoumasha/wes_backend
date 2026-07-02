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
            },
            "conveyor_queue_writer": {
                "sample_count": 5000,
                "metrics": {"write_p95_ms": 18.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 7},
                "thresholds": {"write_p95_ms": 30.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 25},
                "source": {"kind": "postgresql", "evidence": "postgresql://phase3/queue-writer"},
            },
            "ecs_status_command": {
                "sample_count": 2000,
                "metrics": {"status_get_p95_ms": 20.0, "command_post_p95_ms": 24.0},
                "thresholds": {"status_get_p95_ms": 30.0, "command_post_p95_ms": 30.0},
                "source": {"kind": "ecs-http", "evidence": "https://ecs.example.invalid/phase3"},
            },
            "plane_snapshot": {
                "sample_count": 2000,
                "metrics": {"snapshot_p95_ms": 21.0, "snapshot_10x_p95_ms": 70.0},
                "thresholds": {"snapshot_p95_ms": 30.0, "snapshot_10x_p95_ms": 100.0},
                "source": {"kind": "api-http", "evidence": "https://wes.example.invalid/plane-snapshot"},
            },
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_closure_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    p0_e2e_path = _write_json(tmp_path / "phase3-p0-e2e.json", _p0_e2e_artifact())
    benchmark_path = _write_json(tmp_path / "phase3-production-benchmark.json", _benchmark_artifact())
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

    p0_e2e_path, benchmark_path = _write_closure_artifacts(tmp_path)

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path}
    )

    assert validation.valid is True
    assert validation.reason == "OK"


def test_phase3_closure_gate_cli_validates_artifact_set(tmp_path) -> None:
    p0_e2e_path, benchmark_path = _write_closure_artifacts(tmp_path)
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
