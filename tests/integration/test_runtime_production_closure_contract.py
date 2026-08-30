"""Runtime production closure contract."""

from __future__ import annotations

_PLACEHOLDER_EVIDENCE_SHA256 = "0" * 64


def _runtime_production_e2e_artifact() -> dict:
    """生产 P0 E2E 证据的最小有效 artifact。"""

    return {
        "profile": {
            "kind": "production-e2e",
            "environment": "field-dry-run",
            "dependency_profile": "wms-ecs-http",
        },
        "source": {
            "kind": "trace-query",
            "environment": "field-dry-run",
            "evidence": "reports/runtime/p0-e2e/trace-prod-0001.json",
            "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
        },
        "latency": {"p95_seconds": 18.7},
        "recording": {
            "scenario_id": "runtime-production-e2e",
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
        },
        "exception_paths": {
            "ecs_timeout": {
                "result": "RECONCILING",
                "evidence": "reports/runtime/p0-e2e/ecs-timeout.json",
                "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
            },
            "wms_reject": {
                "result": "RECONCILING",
                "evidence": "reports/runtime/p0-e2e/wms-reject.json",
                "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
            },
            "callback_out_of_order": {
                "result": "RECONCILING",
                "evidence": "reports/runtime/p0-e2e/callback-out-of-order.json",
                "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
            },
        },
    }


def test_runtime_production_e2e_gate_accepts_production_trace_evidence() -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    validation = RuntimeP0E2EGate().validate_artifact(_runtime_production_e2e_artifact())

    assert validation.valid is True
    assert validation.reason == "OK"


def test_runtime_production_e2e_gate_rejects_fixture_or_partial_chain_as_production() -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    artifact = _runtime_production_e2e_artifact()
    artifact["profile"]["environment"] = "sandbox"
    artifact["source"]["kind"] = "fixture"
    artifact["recording"]["events"] = artifact["recording"]["events"][:3]

    validation = RuntimeP0E2EGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_PROFILE_METADATA"
    assert validation.invalid_profile_fields == ("profile.environment",)


def test_runtime_production_e2e_gate_rejects_missing_effect_evidence() -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    artifact = _runtime_production_e2e_artifact()
    for event in artifact["recording"]["events"]:
        payload = event.get("payload")
        if isinstance(payload, dict) and str(payload.get("effect_key", "")).startswith("device-command:"):
            payload.pop("effect_key")

    validation = RuntimeP0E2EGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "MISSING_E2E_EFFECTS"
    assert validation.missing_effects == ("device-command",)


def test_runtime_production_e2e_gate_rejects_non_reconciling_exception_path() -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    artifact = _runtime_production_e2e_artifact()
    artifact["exception_paths"]["wms_reject"]["result"] = "SUCCEEDED"

    validation = RuntimeP0E2EGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_EXCEPTION_PATHS"
    assert validation.invalid_exception_paths == ("wms_reject.result",)


def test_runtime_production_e2e_gate_rejects_slow_production_trace() -> None:
    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    artifact = _runtime_production_e2e_artifact()
    artifact["latency"]["p95_seconds"] = 30.1

    validation = RuntimeP0E2EGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "E2E_LATENCY_EXCEEDED"
    assert validation.failed_latency_fields == ("latency.p95_seconds",)


def test_runtime_production_e2e_gate_cli_accepts_production_artifact(tmp_path) -> None:
    import json
    import subprocess
    import sys
    from pathlib import Path

    artifact_path = tmp_path / "runtime-production-e2e.json"
    artifact_path.write_text(
        json.dumps(_runtime_production_e2e_artifact(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_runtime_production_e2e_gate.py",
            str(artifact_path),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Runtime production E2E artifact passed" in result.stdout


def test_runtime_benchmark_gate_lists_all_required_runtime_scenarios() -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    gate = RuntimeBenchmarkGate()

    assert gate.missing_required({"ecs_status_command"}) == ("plane_snapshot",)
    assert gate.missing_required({"ecs_status_command", "plane_snapshot"}) == ()
    ecs_status_command = next(scenario for scenario in gate.scenarios if scenario.name == "ecs_status_command")
    assert "command_post_p95_ms" in ecs_status_command.required_metrics
    assert ecs_status_command.command == "uv run pytest tests/load/test_ecs_status_command_benchmark.py -q"


def test_runtime_benchmark_scenarios_expose_release_gate_blocking_contract() -> None:
    from src.app.runtime.orchestration.benchmark_gate import (
        RuntimeBenchmarkScenario,
        default_runtime_benchmark_scenarios,
    )

    assert all(scenario.blocks_release_gate is True for scenario in default_runtime_benchmark_scenarios())
    non_blocking = RuntimeBenchmarkScenario(
        name="diagnostic_only",
        command="uv run pytest tests/load/test_ecs_status_command_benchmark.py -q",
        required_metrics=frozenset({"status_get_p95_ms"}),
        blocks_release_gate=False,
    )

    assert non_blocking.blocks_release_gate is False


def test_runtime_benchmark_gate_validates_structured_artifact() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is True
    assert validation.reason == "OK"


def test_runtime_benchmark_gate_rejects_complete_artifact_without_profile_metadata() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact.pop("profile", None)

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "MISSING_PROFILE_METADATA"
    assert validation.missing_profile_fields == (
        "profile.concurrency_level",
        "profile.database_backend",
        "profile.dependency_profile",
        "profile.duration_seconds",
        "profile.kind",
    )


def test_runtime_benchmark_gate_rejects_production_profile_without_postgres_and_concurrency() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"] = {
        "kind": "production-scale",
        "database_backend": "sqlite",
        "dependency_profile": "",
        "concurrency_level": 1,
        "duration_seconds": 0,
    }

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_PROFILE_METADATA"
    assert validation.invalid_profile_fields == (
        "profile.concurrency_level",
        "profile.database_backend",
        "profile.dependency_profile",
        "profile.duration_seconds",
    )


def test_runtime_benchmark_gate_rejects_non_numeric_required_metric() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["scenarios"]["ecs_status_command"]["metrics"]["status_get_p95_ms"] = "8.0"

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_METRICS"
    assert validation.invalid_metrics == ("ecs_status_command.status_get_p95_ms",)


def test_runtime_benchmark_gate_rejects_non_numeric_required_threshold() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["scenarios"]["ecs_status_command"]["thresholds"]["status_get_p95_ms"] = None

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_THRESHOLDS"
    assert validation.invalid_thresholds == ("ecs_status_command.status_get_p95_ms",)


def test_runtime_benchmark_gate_rejects_production_artifact_without_scenario_provenance() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"] = {
        "kind": "production-scale",
        "database_backend": "postgresql",
        "dependency_profile": "wms-ecs-simulator",
        "concurrency_level": 64,
        "duration_seconds": 300,
    }

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "MISSING_SCENARIO_PROVENANCE"
    assert validation.missing_provenance_fields == (
        "ecs_status_command.source",
        "plane_snapshot.source",
    )


def test_runtime_benchmark_gate_rejects_production_artifact_without_workload_metadata() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"] = {
        "kind": "production-scale",
        "database_backend": "postgresql",
        "dependency_profile": "postgresql-wms-ecs-http",
        "concurrency_level": 64,
        "duration_seconds": 300,
    }
    artifact["scenarios"]["ecs_status_command"]["source"] = {
        "kind": "ecs-http",
        "evidence": "reports/benchmarks/ecs-status-command.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }
    artifact["scenarios"]["plane_snapshot"]["source"] = {
        "kind": "api-http",
        "evidence": "reports/benchmarks/plane-snapshot.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "MISSING_WORKLOAD_METADATA"
    assert validation.missing_workload_fields == (
        "ecs_status_command.workload.command_post_count",
        "ecs_status_command.workload.status_get_count",
        "plane_snapshot.workload.active_object_count",
        "plane_snapshot.workload.active_session_count",
        "plane_snapshot.workload.device_count",
        "plane_snapshot.workload.queue_count",
        "plane_snapshot.workload.workline_count",
    )


def test_runtime_benchmark_gate_accepts_production_artifact_with_scenario_provenance() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"] = {
        "kind": "production-scale",
        "database_backend": "postgresql",
        "dependency_profile": "wms-ecs-simulator",
        "concurrency_level": 64,
        "duration_seconds": 300,
    }
    artifact["scenarios"]["ecs_status_command"]["source"] = {
        "kind": "ecs-http",
        "evidence": "reports/benchmarks/ecs-status-command.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }
    artifact["scenarios"]["ecs_status_command"]["workload"] = {
        "status_get_count": 400,
        "command_post_count": 400,
    }
    artifact["scenarios"]["plane_snapshot"]["source"] = {
        "kind": "api-http",
        "evidence": "reports/benchmarks/plane-snapshot.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }
    artifact["scenarios"]["plane_snapshot"]["workload"] = {
        "workline_count": 1,
        "queue_count": 10,
        "device_count": 50,
        "active_session_count": 100,
        "active_object_count": 200,
    }

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is True


def test_runtime_benchmark_gate_rejects_unknown_profile_kind() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"]["kind"] = "sandbox"

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_PROFILE_METADATA"
    assert validation.invalid_profile_fields == ("profile.kind",)


def test_runtime_benchmark_runner_generates_gate_valid_artifact() -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate
    from tests.load.runtime_benchmark_scenarios import build_runtime_benchmark_artifact

    artifact = build_runtime_benchmark_artifact(
        environment="ci-lightweight",
        generated_at="2026-07-02T12:00:00Z",
    )
    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is True
    assert validation.reason == "OK"
    assert set(artifact["scenarios"]) == {
        "ecs_status_command",
        "plane_snapshot",
    }
    assert all(result["sample_count"] > 0 for result in artifact["scenarios"].values())


def test_runtime_benchmark_cli_writes_gate_valid_artifact(tmp_path) -> None:
    import json
    import subprocess
    import sys
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[2]
    output_path = tmp_path / "runtime-benchmark.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/run_runtime_benchmarks.py",
            "--output",
            str(output_path),
            "--environment",
            "ci-lightweight",
            "--generated-at",
            "2026-07-02T12:00:00Z",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is True
    assert artifact["environment"] == "ci-lightweight"


def test_runtime_benchmark_gate_rejects_incomplete_artifact() -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    validation = RuntimeBenchmarkGate().validate_artifact(
        {
            "environment": "local-postgres-redis-ecs-simulator",
            "generated_at": "2026-07-02T12:00:00Z",
            "scenarios": {
                "ecs_status_command": {
                    "sample_count": 1000,
                    "metrics": {"status_get_p95_ms": 4.0},
                    "thresholds": {"status_get_p95_ms": 30.0},
                }
            },
        }
    )

    assert validation.valid is False
    assert validation.reason == "MISSING_SCENARIOS"
    assert validation.missing_scenarios == ("plane_snapshot",)
