"""Runtime production closure contract."""

from __future__ import annotations

from datetime import timedelta

from src.utils.timezone import timezone

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
    repo_root = Path(__file__).resolve().parents[3]

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


def test_runtime_production_minimal_chain_records_runtime_effects_and_reconciliation() -> None:
    """P0 闭环必须串起 manifest/session/inbox/intent/device/WMS/plane/reconciliation。"""

    from src.app.runtime.orchestration.scenario_replay import (
        ScenarioEvent,
        ScenarioRecorder,
        ScenarioReplayRunner,
    )

    events = [
        ScenarioEvent(
            event_id="manifest-001",
            kind="workline_manifest",
            occurred_at="2026-07-02T10:00:00Z",
            payload={"object_key": "workline:WL-1", "state": "ACTIVE"},
        ),
        ScenarioEvent(
            event_id="session-001",
            kind="execution_session",
            occurred_at="2026-07-02T10:00:01Z",
            payload={"object_key": "session:S-1", "state": "RUNNING"},
        ),
        ScenarioEvent(
            event_id="inbox-001",
            kind="runtime_inbox",
            occurred_at="2026-07-02T10:00:02Z",
            payload={"source_event_id": "ecs-scan-1", "object_key": "pkg:PKG-0001", "state": "RECEIVED"},
        ),
        ScenarioEvent(
            event_id="intent-001",
            kind="runtime_intent",
            occurred_at="2026-07-02T10:00:03Z",
            payload={"effect_key": "device-command:CMD-1", "object_key": "pkg:PKG-0001", "state": "DISPATCHING"},
        ),
        ScenarioEvent(
            event_id="device-001",
            kind="device_command",
            occurred_at="2026-07-02T10:00:04Z",
            payload={"effect_key": "device-command:CMD-1", "object_key": "pkg:PKG-0001", "state": "ACKED"},
        ),
        ScenarioEvent(
            event_id="wms-001",
            kind="wms_fulfillment",
            occurred_at="2026-07-02T10:00:05Z",
            payload={"effect_key": "wms-fulfillment:WMS-1", "object_key": "pkg:PKG-0001", "state": "SUCCEEDED"},
        ),
        ScenarioEvent(
            event_id="plane-001",
            kind="plane_snapshot",
            occurred_at="2026-07-02T10:00:06Z",
            payload={"object_key": "pkg:PKG-0001", "state": "VISIBLE"},
        ),
        ScenarioEvent(
            event_id="recon-001",
            kind="runtime_conflict",
            occurred_at="2026-07-02T10:00:07Z",
            payload={"object_key": "pkg:PKG-0001", "state": "RECONCILING", "reason": "callback_out_of_order"},
        ),
    ]

    recording = ScenarioRecorder().record(scenario_id="runtime-production-minimal", events=events)
    result = ScenarioReplayRunner().replay(recording)

    assert result.timeline == tuple(f"{event.kind}:{event.event_id}" for event in recording.events)
    assert result.outbox_effect_keys == ("device-command:CMD-1", "wms-fulfillment:WMS-1")
    assert result.reconciliation_reasons == ("callback_out_of_order",)
    assert len(result.projection_hash) == 64


def test_runtime_device_timeout_and_wms_reject_enter_reconciling_without_silent_success() -> None:
    """ECS 超时与 WMS 拒绝必须进入 RECONCILING, 不能静默成功。"""

    from src.app.reconciliation.manager import ReconciliationConflictInput, ReconciliationManager
    from src.app.runtime.orchestration.services.device_dispatch_policy import (
        DeviceDispatchDecisionKind,
        DeviceDispatchPolicy,
        DeviceDispatchRequest,
        DeviceRuntimeSnapshot,
        DeviceRuntimeStatus,
    )
    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    now = timezone.now_for_db()
    device_decision = DeviceDispatchPolicy().evaluate(
        DeviceDispatchRequest(
            command_code="CMD-TIMEOUT",
            device_role="scanner",
            capability_code="SCAN",
            dispatch_deadline_at=now,
        ),
        snapshot=DeviceRuntimeSnapshot(
            device_code="DEV-1",
            status=DeviceRuntimeStatus.RUNNING,
            observed_at=now,
            status_valid_until=now + timedelta(milliseconds=1000),
        ),
        now=now + timedelta(milliseconds=1),
    )
    wms_result = WmsFulfillmentStateMachine().transition(
        current=FulfillmentState.SENT,
        event=FulfillmentEvent.PROVIDER_REJECTED,
        now=now,
    )
    reconciliation = ReconciliationManager().register_conflict(
        ReconciliationConflictInput(
            owner_domain="runtime",
            owner_kind="ExecutionSession",
            owner_id="session-timeout",
            conflict_kind="DEVICE_TIMEOUT_WMS_REJECT",
            reason="device timeout and WMS business reject require manual resolution",
            evidence_refs=["device:CMD-TIMEOUT", "wms:reject"],
            detected_at=now,
        )
    )

    assert device_decision.kind == DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD
    assert device_decision.runtime_hold_required is True
    assert wms_result.state == FulfillmentState.REJECTED
    assert reconciliation.runtime_hold_required is True
    assert reconciliation.status == "PENDING"


def test_runtime_benchmark_gate_lists_all_required_runtime_scenarios() -> None:
    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    gate = RuntimeBenchmarkGate()

    assert gate.missing_required({"runtime_inbox_claim"}) == (
        "conveyor_queue_writer",
        "ecs_status_command",
        "plane_snapshot",
    )
    assert (
        gate.missing_required(
            {
                "runtime_inbox_claim",
                "conveyor_queue_writer",
                "ecs_status_command",
                "plane_snapshot",
            }
        )
        == ()
    )
    conveyor_queue_writer = next(scenario for scenario in gate.scenarios if scenario.name == "conveyor_queue_writer")
    assert "integrity_conflict_recheck_count" in conveyor_queue_writer.required_metrics


def test_runtime_benchmark_gate_validates_structured_artifact() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[3]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is True
    assert validation.reason == "OK"


def test_runtime_benchmark_gate_rejects_complete_artifact_without_profile_metadata() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[3]
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

    repo_root = Path(__file__).resolve().parents[3]
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

    repo_root = Path(__file__).resolve().parents[3]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["scenarios"]["runtime_inbox_claim"]["metrics"]["claim_p95_ms"] = "4.0"

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_METRICS"
    assert validation.invalid_metrics == ("runtime_inbox_claim.claim_p95_ms",)


def test_runtime_benchmark_gate_rejects_non_numeric_required_threshold() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[3]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["scenarios"]["runtime_inbox_claim"]["thresholds"]["claim_p95_ms"] = None

    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    assert validation.valid is False
    assert validation.reason == "INVALID_THRESHOLDS"
    assert validation.invalid_thresholds == ("runtime_inbox_claim.claim_p95_ms",)


def test_runtime_benchmark_gate_rejects_production_artifact_without_scenario_provenance() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[3]
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
        "conveyor_queue_writer.source",
        "ecs_status_command.source",
        "plane_snapshot.source",
        "runtime_inbox_claim.source",
    )


def test_runtime_benchmark_gate_rejects_production_artifact_without_workload_metadata() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[3]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"] = {
        "kind": "production-scale",
        "database_backend": "postgresql",
        "dependency_profile": "postgresql-wms-ecs-http",
        "concurrency_level": 64,
        "duration_seconds": 300,
    }
    artifact["scenarios"]["runtime_inbox_claim"]["source"] = {
        "kind": "postgresql",
        "evidence": "reports/benchmarks/runtime-inbox-claim.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }
    artifact["scenarios"]["conveyor_queue_writer"]["source"] = {
        "kind": "postgresql",
        "evidence": "reports/benchmarks/conveyor-queue-writer.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
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
        "conveyor_queue_writer.workload.active_membership_count",
        "conveyor_queue_writer.workload.concurrent_identity_collision",
        "ecs_status_command.workload.command_post_count",
        "ecs_status_command.workload.status_get_count",
        "plane_snapshot.workload.active_object_count",
        "plane_snapshot.workload.active_session_count",
        "plane_snapshot.workload.device_count",
        "plane_snapshot.workload.queue_count",
        "plane_snapshot.workload.workline_count",
        "runtime_inbox_claim.workload.pending_inbox_count",
        "runtime_inbox_claim.workload.worker_concurrency",
    )


def test_runtime_benchmark_gate_accepts_production_artifact_with_scenario_provenance() -> None:
    import json
    from pathlib import Path

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate

    repo_root = Path(__file__).resolve().parents[3]
    artifact = json.loads((repo_root / "tests" / "load" / "fixtures" / "runtime_benchmark_artifact.json").read_text())
    artifact["profile"] = {
        "kind": "production-scale",
        "database_backend": "postgresql",
        "dependency_profile": "wms-ecs-simulator",
        "concurrency_level": 64,
        "duration_seconds": 300,
    }
    artifact["scenarios"]["runtime_inbox_claim"]["source"] = {
        "kind": "postgresql",
        "evidence": "reports/benchmarks/runtime-inbox-claim.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }
    artifact["scenarios"]["runtime_inbox_claim"]["workload"] = {
        "pending_inbox_count": 1000,
        "worker_concurrency": 4,
    }
    artifact["scenarios"]["conveyor_queue_writer"]["source"] = {
        "kind": "postgresql",
        "evidence": "reports/benchmarks/conveyor-queue-writer.json",
        "evidence_sha256": _PLACEHOLDER_EVIDENCE_SHA256,
    }
    artifact["scenarios"]["conveyor_queue_writer"]["workload"] = {
        "active_membership_count": 200,
        "concurrent_identity_collision": True,
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

    repo_root = Path(__file__).resolve().parents[3]
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
        "runtime_inbox_claim",
        "conveyor_queue_writer",
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

    repo_root = Path(__file__).resolve().parents[3]
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
                "runtime_inbox_claim": {
                    "sample_count": 1000,
                    "metrics": {"claim_p95_ms": 4.0},
                    "thresholds": {"claim_p95_ms": 30.0},
                }
            },
        }
    )

    assert validation.valid is False
    assert validation.reason == "MISSING_SCENARIOS"
    assert validation.missing_scenarios == (
        "conveyor_queue_writer",
        "ecs_status_command",
        "plane_snapshot",
    )
