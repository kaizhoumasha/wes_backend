"""Runtime closure evidence gate contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_PLACEHOLDER_EVIDENCE_SHA256 = "0" * 64


def _p0_e2e_artifact() -> dict[str, Any]:
    return {
        "profile": {
            "kind": "production-e2e",
            "environment": "field-dry-run",
            "dependency_profile": "wms-ecs-http",
        },
        "source": {
            "kind": "trace-query",
            "environment": "field-dry-run",
            "evidence": "trace-query://runtime/p0-e2e",
            "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
        },
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
            "callback_out_of_order": {
                "result": "RECONCILING",
                "evidence": "trace-query://runtime/callback",
                "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
            },
            "ecs_timeout": {
                "result": "RECONCILING",
                "evidence": "trace-query://runtime/ecs-timeout",
                "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
            },
            "wms_reject": {
                "result": "RECONCILING",
                "evidence": "trace-query://runtime/wms-reject",
                "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
            },
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
                "source": {
                    "kind": "postgresql",
                    "evidence": "postgresql://runtime/runtime-inbox-claim",
                    "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
                },
                "workload": {"pending_inbox_count": 1000, "worker_concurrency": 4},
            },
            "conveyor_queue_writer": {
                "sample_count": 5000,
                "metrics": {"write_p95_ms": 18.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 7},
                "thresholds": {"write_p95_ms": 30.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 25},
                "source": {
                    "kind": "postgresql",
                    "evidence": "postgresql://runtime/queue-writer",
                    "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
                },
                "workload": {"active_membership_count": 200, "concurrent_identity_collision": True},
            },
            "ecs_status_command": {
                "sample_count": 2000,
                "metrics": {"status_get_p95_ms": 20.0, "command_post_p95_ms": 24.0},
                "thresholds": {"status_get_p95_ms": 30.0, "command_post_p95_ms": 30.0},
                "source": {
                    "kind": "ecs-http",
                    "evidence": "https://ecs.example.invalid/runtime",
                    "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
                },
                "workload": {"status_get_count": 400, "command_post_count": 400},
            },
            "plane_snapshot": {
                "sample_count": 2000,
                "metrics": {"snapshot_p95_ms": 21.0, "snapshot_10x_p95_ms": 70.0},
                "thresholds": {"snapshot_p95_ms": 30.0, "snapshot_10x_p95_ms": 100.0},
                "source": {
                    "kind": "api-http",
                    "evidence": "https://wes.example.invalid/plane-snapshot",
                    "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
                },
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
    p0_e2e_path = _write_json(tmp_path / "runtime-production-e2e.json", _p0_e2e_artifact())
    benchmark_path = _write_json(tmp_path / "runtime-production-benchmark.json", _benchmark_artifact())
    return p0_e2e_path, benchmark_path


def _write_closure_artifacts_with_evidence(tmp_path: Path) -> tuple[Path, Path]:
    p0_e2e_artifact = _p0_e2e_artifact()
    source_evidence_path = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/source.json",
        p0_e2e_artifact["recording"],
    )
    p0_e2e_artifact["source"]["evidence"] = source_evidence_path
    p0_e2e_artifact["source"]["evidence_sha256"] = hashlib.sha256(
        (tmp_path / source_evidence_path).read_bytes()
    ).hexdigest()
    callback_evidence_path = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/callback-out-of-order.json",
        {"case": "callback_out_of_order", "result": "RECONCILING"},
    )
    p0_e2e_artifact["exception_paths"]["callback_out_of_order"]["evidence"] = callback_evidence_path
    p0_e2e_artifact["exception_paths"]["callback_out_of_order"]["evidence_sha256"] = hashlib.sha256(
        (tmp_path / callback_evidence_path).read_bytes()
    ).hexdigest()
    ecs_timeout_evidence_path = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/ecs-timeout.json",
        {"case": "ecs_timeout", "result": "RECONCILING"},
    )
    p0_e2e_artifact["exception_paths"]["ecs_timeout"]["evidence"] = ecs_timeout_evidence_path
    p0_e2e_artifact["exception_paths"]["ecs_timeout"]["evidence_sha256"] = hashlib.sha256(
        (tmp_path / ecs_timeout_evidence_path).read_bytes()
    ).hexdigest()
    wms_reject_evidence_path = _write_evidence_file(
        tmp_path,
        "evidence/p0-e2e/wms-reject.json",
        {"case": "wms_reject", "result": "RECONCILING"},
    )
    p0_e2e_artifact["exception_paths"]["wms_reject"]["evidence"] = wms_reject_evidence_path
    p0_e2e_artifact["exception_paths"]["wms_reject"]["evidence_sha256"] = hashlib.sha256(
        (tmp_path / wms_reject_evidence_path).read_bytes()
    ).hexdigest()

    benchmark_artifact = _benchmark_artifact()
    for scenario_name in benchmark_artifact["scenarios"]:
        evidence_path = _write_evidence_file(
            tmp_path,
            f"evidence/benchmark/{scenario_name}.json",
            benchmark_artifact["scenarios"][scenario_name],
        )
        benchmark_artifact["scenarios"][scenario_name]["source"]["evidence"] = evidence_path
        benchmark_artifact["scenarios"][scenario_name]["source"]["evidence_sha256"] = hashlib.sha256(
            (tmp_path / evidence_path).read_bytes()
        ).hexdigest()

    p0_e2e_path = _write_json(tmp_path / "runtime-production-e2e.json", p0_e2e_artifact)
    benchmark_path = _write_json(tmp_path / "runtime-production-benchmark.json", benchmark_artifact)
    return p0_e2e_path, benchmark_path


def test_production_closure_gate_requires_both_production_artifacts(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path = _write_json(tmp_path / "runtime-production-e2e.json", _p0_e2e_artifact())

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "MISSING_PRODUCTION_CLOSURE_ARTIFACTS"
    assert validation.missing_artifacts == ("benchmark",)


def test_production_closure_gate_defaults_to_mock_without_real_artifacts() -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    validation = RuntimeProductionClosureGate().validate_artifact_files({})

    assert validation.valid is True
    assert validation.reason == "MOCK_PRODUCTION_CLOSURE"


def test_production_closure_gate_accepts_valid_p0_and_benchmark_artifacts(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is True
    assert validation.reason == "OK"


def test_production_closure_gate_rejects_missing_referenced_evidence_files(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts(tmp_path)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "MISSING_PRODUCTION_CLOSURE_EVIDENCE_FILES"
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


def test_production_closure_gate_rejects_mismatched_referenced_evidence_files(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)
    (tmp_path / "evidence/p0-e2e/source.json").write_text(
        json.dumps({"events": []}, sort_keys=True),
        encoding="utf-8",
    )

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "MISMATCHED_PRODUCTION_CLOSURE_EVIDENCE_FILES"
    assert validation.mismatched_evidence_files == (
        "p0_e2e:source.evidence",
        "p0_e2e:source.evidence_sha256",
    )


def test_production_closure_gate_rejects_mismatched_exception_evidence_case(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)
    wrong_case_path = tmp_path / "evidence/p0-e2e/ecs-timeout.json"
    wrong_case_path.write_text(
        json.dumps({"case": "wms_reject", "result": "RECONCILING"}, sort_keys=True),
        encoding="utf-8",
    )
    p0_artifact = json.loads(p0_e2e_path.read_text(encoding="utf-8"))
    p0_artifact["exception_paths"]["ecs_timeout"]["evidence_sha256"] = hashlib.sha256(
        wrong_case_path.read_bytes()
    ).hexdigest()
    _write_json(p0_e2e_path, p0_artifact)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "MISMATCHED_PRODUCTION_CLOSURE_EVIDENCE_FILES"
    assert validation.mismatched_evidence_files == ("p0_e2e:exception_paths.ecs_timeout.evidence",)


def test_production_closure_gate_rejects_reused_exception_evidence_file(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)
    p0_artifact = json.loads(p0_e2e_path.read_text(encoding="utf-8"))
    reused = p0_artifact["exception_paths"]["ecs_timeout"]
    p0_artifact["exception_paths"]["wms_reject"] = dict(reused)
    _write_json(p0_e2e_path, p0_artifact)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "MISMATCHED_PRODUCTION_CLOSURE_EVIDENCE_FILES"
    assert validation.mismatched_evidence_files == (
        "p0_e2e:exception_paths.wms_reject.evidence",
        "p0_e2e:exception_paths.wms_reject.evidence_duplicate",
    )


def test_production_closure_gate_rejects_lightweight_benchmark_artifact(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path = _write_json(tmp_path / "runtime-production-e2e.json", _p0_e2e_artifact())
    benchmark_artifact = _benchmark_artifact()
    benchmark_artifact["environment"] = "local-lightweight"
    benchmark_artifact["profile"] = {
        "kind": "lightweight",
        "database_backend": "in-memory",
        "dependency_profile": "in-process-contract",
        "concurrency_level": 1,
        "duration_seconds": 0,
    }
    benchmark_path = _write_json(tmp_path / "runtime-lightweight-benchmark.json", benchmark_artifact)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "INVALID_PRODUCTION_CLOSURE_ARTIFACTS"
    assert validation.invalid_artifacts == ("benchmark:LIGHTWEIGHT_BENCHMARK_NOT_ALLOWED",)


def test_production_closure_gate_accepts_lightweight_benchmark_in_mock_profile(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path = _write_json(tmp_path / "runtime-production-e2e.json", _p0_e2e_artifact())
    benchmark_artifact = _benchmark_artifact()
    benchmark_artifact["environment"] = "local-lightweight"
    benchmark_artifact["profile"] = {
        "kind": "lightweight",
        "database_backend": "in-memory",
        "dependency_profile": "in-process-contract",
        "concurrency_level": 1,
        "duration_seconds": 0,
    }
    benchmark_path = _write_json(tmp_path / "runtime-lightweight-benchmark.json", benchmark_artifact)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="mock",
    )

    assert validation.valid is True
    assert validation.reason == "MOCK_PRODUCTION_CLOSURE"


def test_production_closure_gate_rejects_non_production_benchmark_environment(tmp_path) -> None:
    from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

    p0_e2e_path = _write_json(tmp_path / "runtime-production-e2e.json", _p0_e2e_artifact())
    benchmark_artifact = _benchmark_artifact()
    benchmark_artifact["environment"] = "local-lightweight"
    benchmark_path = _write_json(tmp_path / "runtime-local-benchmark.json", benchmark_artifact)

    validation = RuntimeProductionClosureGate().validate_artifact_files(
        {"p0_e2e": p0_e2e_path, "benchmark": benchmark_path},
        closure_profile="production",
    )

    assert validation.valid is False
    assert validation.reason == "INVALID_PRODUCTION_CLOSURE_ARTIFACTS"
    assert validation.invalid_artifacts == ("benchmark:NON_PRODUCTION_BENCHMARK_ENVIRONMENT",)


def test_production_closure_gate_cli_validates_artifact_set(tmp_path) -> None:
    p0_e2e_path, benchmark_path = _write_closure_artifacts_with_evidence(tmp_path)
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_runtime_production_closure_gate.py",
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

    assert "Runtime production closure evidence passed" in result.stdout


def test_production_closure_gate_cli_defaults_to_mock_without_artifacts() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_runtime_production_closure_gate.py",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Runtime production closure mock evidence passed" in result.stdout
