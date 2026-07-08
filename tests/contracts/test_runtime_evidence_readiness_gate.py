"""Runtime evidence readiness gate contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_runtime_evidence_readiness_gate.py"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_runtime_evidence_files(base_dir: Path) -> None:
    for relative_path in (
        "provider-contracts/sorter-inbound.json",
        "provider-contracts/smt-ng-wms-reconciliation.json",
        "traces/effect-dispatch.json",
        "traces/runtime-inbox-worker.json",
        "traces/runtime-hold-reconciliation.json",
        "benchmarks/runtime-evidence.json",
    ):
        _write_json(base_dir / relative_path, {"evidence": relative_path, "result": "PASS"})


def _runtime_manifest_entry(artifact: dict[str, Any], dotted_key: str) -> dict[str, Any]:
    current: Any = artifact["evidence_manifest"]
    for key_part in dotted_key.split("."):
        current = current[key_part]
    return current


def _add_runtime_manifest_hashes(artifact: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    for manifest_key in (
        "provider_contracts.sorter_inbound",
        "provider_contracts.smt_ng_wms_reconciliation",
        "effect_dispatch_trace",
        "callback_worker_trace",
        "runtime_hold_reconciliation_trace",
        "benchmark",
    ):
        entry = _runtime_manifest_entry(artifact, manifest_key)
        evidence_path = Path(entry["evidence"])
        if not evidence_path.is_absolute():
            evidence_path = artifact_dir / evidence_path
        entry["evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    return artifact


def _runtime_evidence_artifact(*, profile: str, evidence_dir: str | None = None) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "profile": {"name": profile},
        "capabilities": ["sorter_inbound", "smt_ng_wms_reconciliation"],
        "effect_path": [
            "RuntimeIntentLog",
            "WmsFulfillmentPort.notify_pkg_binding",
            "WmsInventoryTransactionPort.confirm_inbound",
        ],
        "callback_path": ["RuntimeInbox"],
        "service_behavior_invariant": ["provider-contract"],
    }
    if evidence_dir is not None:
        artifact["evidence_manifest"] = {
            "provider_contracts": {
                "sorter_inbound": {
                    "kind": "provider-contract",
                    "evidence": f"{evidence_dir}/provider-contracts/sorter-inbound.json",
                },
                "smt_ng_wms_reconciliation": {
                    "kind": "provider-contract",
                    "evidence": f"{evidence_dir}/provider-contracts/smt-ng-wms-reconciliation.json",
                },
            },
            "effect_dispatch_trace": {
                "kind": "runtime-trace",
                "evidence": f"{evidence_dir}/traces/effect-dispatch.json",
            },
            "callback_worker_trace": {
                "kind": "runtime-trace",
                "evidence": f"{evidence_dir}/traces/runtime-inbox-worker.json",
            },
            "runtime_hold_reconciliation_trace": {
                "kind": "runtime-trace",
                "evidence": f"{evidence_dir}/traces/runtime-hold-reconciliation.json",
            },
            "benchmark": {
                "kind": "runtime-evidence-benchmark",
                "evidence": f"{evidence_dir}/benchmarks/runtime-evidence.json",
            },
        }
    return artifact


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
            "evidence": "evidence/p0-e2e/source.json",
        },
        "latency": {"p95_seconds": 18.7},
        "recording": {
            "events": [
                {"kind": "workline_manifest", "payload": {"object_key": "workline:WL-PROD"}},
                {"kind": "execution_session", "payload": {"object_key": "session:S-PROD"}},
                {"kind": "runtime_inbox", "payload": {"source_event_id": "ecs-scan-prod-1"}},
                {"kind": "runtime_intent", "payload": {"effect_key": "device-command:CMD-PROD-1"}},
                {"kind": "device_command", "payload": {"effect_key": "device-command:CMD-PROD-1"}},
                {"kind": "wms_fulfillment", "payload": {"effect_key": "wms-fulfillment:FUL-PROD-1"}},
                {"kind": "plane_snapshot", "payload": {"object_key": "pkg:PKG-PROD-0001"}},
            ],
        },
        "exception_paths": {
            "callback_out_of_order": {"result": "RECONCILING", "evidence": "evidence/p0-e2e/callback.json"},
            "ecs_timeout": {"result": "RECONCILING", "evidence": "evidence/p0-e2e/ecs-timeout.json"},
            "wms_reject": {"result": "RECONCILING", "evidence": "evidence/p0-e2e/wms-reject.json"},
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
                "source": {"kind": "postgresql", "evidence": "evidence/benchmark/runtime_inbox_claim.json"},
                "workload": {"pending_inbox_count": 1000, "worker_concurrency": 4},
            },
            "conveyor_queue_writer": {
                "sample_count": 5000,
                "metrics": {"write_p95_ms": 18.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 7},
                "thresholds": {"write_p95_ms": 30.0, "reconciling_count": 0, "integrity_conflict_recheck_count": 25},
                "source": {"kind": "postgresql", "evidence": "evidence/benchmark/conveyor_queue_writer.json"},
                "workload": {"active_membership_count": 200, "concurrent_identity_collision": True},
            },
            "ecs_status_command": {
                "sample_count": 2000,
                "metrics": {"status_get_p95_ms": 20.0, "command_post_p95_ms": 24.0},
                "thresholds": {"status_get_p95_ms": 30.0, "command_post_p95_ms": 30.0},
                "source": {"kind": "ecs-http", "evidence": "evidence/benchmark/ecs_status_command.json"},
                "workload": {"status_get_count": 400, "command_post_count": 400},
            },
            "plane_snapshot": {
                "sample_count": 2000,
                "metrics": {"snapshot_p95_ms": 21.0, "snapshot_10x_p95_ms": 70.0},
                "thresholds": {"snapshot_p95_ms": 30.0, "snapshot_10x_p95_ms": 100.0},
                "source": {"kind": "api-http", "evidence": "evidence/benchmark/plane_snapshot.json"},
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


def _write_runtime_production_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    p0_artifact = _p0_e2e_artifact()
    source_path = _write_json(tmp_path / "evidence/p0-e2e/source.json", p0_artifact["recording"])
    p0_artifact["source"]["evidence_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    callback_path = _write_json(
        tmp_path / "evidence/p0-e2e/callback.json", {"case": "callback_out_of_order", "result": "RECONCILING"}
    )
    p0_artifact["exception_paths"]["callback_out_of_order"]["evidence_sha256"] = hashlib.sha256(
        callback_path.read_bytes()
    ).hexdigest()
    ecs_timeout_path = _write_json(
        tmp_path / "evidence/p0-e2e/ecs-timeout.json", {"case": "ecs_timeout", "result": "RECONCILING"}
    )
    p0_artifact["exception_paths"]["ecs_timeout"]["evidence_sha256"] = hashlib.sha256(
        ecs_timeout_path.read_bytes()
    ).hexdigest()
    wms_reject_path = _write_json(
        tmp_path / "evidence/p0-e2e/wms-reject.json", {"case": "wms_reject", "result": "RECONCILING"}
    )
    p0_artifact["exception_paths"]["wms_reject"]["evidence_sha256"] = hashlib.sha256(
        wms_reject_path.read_bytes()
    ).hexdigest()

    benchmark_artifact = _benchmark_artifact()
    for scenario_name, scenario in benchmark_artifact["scenarios"].items():
        evidence_path = _write_json(tmp_path / f"evidence/benchmark/{scenario_name}.json", scenario)
        scenario["source"]["evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()

    return (
        _write_json(tmp_path / "runtime-production-e2e.json", p0_artifact),
        _write_json(tmp_path / "runtime-production-benchmark.json", benchmark_artifact),
    )


def _write_minimal_runtime_docs(repo_root: Path, *, stale_status: bool = False) -> None:
    docs_architecture = repo_root / "docs" / "architecture"
    docs_architecture.mkdir(parents=True, exist_ok=True)
    (repo_root / "docs" / "superpowers" / "plans").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests" / "mock" / "material_flow").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests" / "workline_runtime").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "app" / "runtime" / "capabilities" / "material_flow").mkdir(parents=True, exist_ok=True)

    status_by_file = {
        "cell-reservation-spec.md": "CellReservation 开发/测试已落地；生产投放热路径未接入",
        "material-location-query-spec.md": "MaterialLocationQuery 开发/测试已落地",
        "workline-active-objects-spec.md": "WorklineActiveObjects 开发/测试已落地",
        "sorter-inbound-capability-spec.md": "sorter inbound runtime capability 已落地；evidence profile 未闭合",
        "smt-ng-wms-reconciliation-spec.md": "SMT/NG/WMS reconciliation runtime capability 已落地；evidence profile 未闭合",
    }
    if stale_status:
        status_by_file["cell-reservation-spec.md"] = "CellReservation 前置设计 SPEC，未实现"

    for filename, status in status_by_file.items():
        (docs_architecture / filename).write_text(
            f"# {filename}\n\n> 状态：{status}\n> 父计划：mock\n",
            encoding="utf-8",
        )

    (repo_root / "docs" / "superpowers" / "plans" / "2026-07-04-runtime-evidence-readiness.md").write_text(
        """# material-flow Runtime Readiness 实施计划
sorter inbound 与 SMT/NG/WMS reconciliation 后续目标是 production-capable runtime path，外部 provider 可替换。
开发/测试范围的 Runtime evidence readiness gate 已关闭。
- [x] sorter inbound runtime capability builder：已输出 RuntimeIntent/effect contract/evidence。
- [x] SMT/NG/WMS reconciliation runtime capability builder：已输出 RuntimeIntent/RuntimeInbox evidence/RuntimeHold plan。
- [x] sorter inbound evidence profile gate：site/production manifest 已要求 provider contract 证据。
- [x] SMT/NG/WMS reconciliation evidence profile gate：site/production manifest 已要求 RuntimeInbox worker trace。
证据文件本身属于 reports/、CI 或部署验收产物。
""",
        encoding="utf-8",
    )
    (repo_root / "docs" / "architecture" / "workline-and-plugin-restructuring.md").write_text(
        """### 10.5 Material-flow target capabilities
sorter inbound 与 SMT/NG/WMS reconciliation 后续目标是 production-capable runtime path，外部 provider 可替换。
site/production evidence manifest gate 只改变验收证据要求，不改变 service 行为。
- [x] 分拣机/粗分机入库能力 runtime capability builder 已按目标态 capability / port 重建，不保留旧插件兼容入口
### 10.6 target-state
""",
        encoding="utf-8",
    )
    (repo_root / "tests" / "mock" / "material_flow" / "test_material_flow_mock_acceptance.py").write_text(
        '"""本机 MOCK 验收，不代表 evidence profile 闭合。"""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "mock" / "material_flow" / "test_sorter_inbound_mock_contracts.py").write_text(
        '"""sorter inbound 本机 MOCK 合同，不代表 evidence profile 闭合。"""\n',
        encoding="utf-8",
    )
    (
        repo_root / "src" / "app" / "runtime" / "capabilities" / "material_flow" / "sorter_inbound_preview_service.py"
    ).write_text(
        '"""Material-flow sorter inbound preview capability; LOCAL_MOCK_ONLY; production_write_path; legacy_plugin_entry_used."""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_sorter_inbound_preview_service.py").write_text(
        (
            '"""Material-flow sorter inbound preview capability 合同。"""\n'
            '"production_write_path legacy_plugin_entry_used WmsFulfillmentPort.notify_pkg_binding '
            'WmsInventoryTransactionPort.confirm_inbound"\n'
        ),
        encoding="utf-8",
    )
    (
        repo_root
        / "src"
        / "app"
        / "runtime"
        / "capabilities"
        / "material_flow"
        / "smt_ng_wms_reconciliation_preview_service.py"
    ).write_text(
        '"""material-flow SMT/NG/WMS preview; LOCAL_MOCK_ONLY; production_write_path; legacy_plugin_entry_used; RuntimeHold."""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_smt_ng_wms_reconciliation_preview_service.py").write_text(
        (
            '"""material-flow SMT/NG/WMS preview capability 合同。"""\n'
            '"production_write_path legacy_plugin_entry_used IDEMPOTENT_DUPLICATE RuntimeHold scope-only"\n'
        ),
        encoding="utf-8",
    )
    (
        repo_root / "src" / "app" / "runtime" / "capabilities" / "material_flow" / "sorter_inbound_runtime_service.py"
    ).write_text(
        (
            '"""Material-flow sorter inbound runtime capability."""\n'
            '"RuntimeIntent WmsFulfillmentPort.notify_pkg_binding '
            'WmsInventoryTransactionPort.confirm_inbound provider-contract"\n'
        ),
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_sorter_inbound_runtime_service.py").write_text(
        (
            '"""Material-flow sorter inbound runtime capability 合同。"""\n'
            '"RuntimeIntent WmsFulfillmentPort.notify_pkg_binding '
            'WmsInventoryTransactionPort.confirm_inbound provider-contract"\n'
        ),
        encoding="utf-8",
    )
    (
        repo_root
        / "src"
        / "app"
        / "runtime"
        / "capabilities"
        / "material_flow"
        / "smt_ng_wms_reconciliation_runtime_service.py"
    ).write_text(
        '"""Material-flow SMT/NG/WMS reconciliation runtime capability; RuntimeIntent RuntimeInbox provider-contract."""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_smt_ng_wms_reconciliation_runtime_service.py").write_text(
        '"""Material-flow SMT/NG/WMS reconciliation runtime capability 合同。 RuntimeIntent RuntimeInbox provider-contract."""\n',
        encoding="utf-8",
    )


def test_runtime_evidence_readiness_gate_dev_mock_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "MOCK_RUNTIME_EVIDENCE_READINESS" in result.stdout


def test_runtime_evidence_readiness_gate_production_profile_is_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--readiness-profile", "production"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MISSING_RUNTIME_EVIDENCE_ARTIFACT" in result.stdout
    assert "evidence_profile=production" in result.stdout


def test_runtime_evidence_readiness_gate_simulator_profile_requires_evidence_not_code_branch() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--readiness-profile", "simulator"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MISSING_RUNTIME_EVIDENCE_ARTIFACT" in result.stdout
    assert "evidence_profile=simulator" in result.stdout


def test_runtime_evidence_readiness_gate_simulator_profile_accepts_provider_contract_evidence(tmp_path) -> None:
    evidence_artifact = tmp_path / "runtime-evidence.json"
    evidence_artifact.write_text(
        """{
  "profile": {"name": "simulator"},
  "capabilities": ["sorter_inbound", "smt_ng_wms_reconciliation"],
  "effect_path": [
    "RuntimeIntentLog",
    "WmsFulfillmentPort.notify_pkg_binding",
    "WmsInventoryTransactionPort.confirm_inbound"
  ],
  "callback_path": ["RuntimeInbox"],
  "service_behavior_invariant": ["provider-contract"]
}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "simulator",
            "--runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "RUNTIME_EVIDENCE_READY" in result.stdout
    assert "evidence_profile=simulator" in result.stdout


def test_runtime_evidence_readiness_gate_site_profile_requires_evidence_manifest(tmp_path) -> None:
    evidence_artifact = _write_json(
        tmp_path / "runtime-evidence-site.json",
        _runtime_evidence_artifact(profile="site"),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "site",
            "--runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_RUNTIME_EVIDENCE_MANIFEST" in result.stdout


def test_runtime_evidence_readiness_gate_site_profile_accepts_evidence_manifest_files(tmp_path) -> None:
    evidence_dir = tmp_path / "evidence" / "runtime-evidence"
    _write_runtime_evidence_files(evidence_dir)
    evidence_artifact = _write_json(
        tmp_path / "runtime-evidence-site.json",
        _add_runtime_manifest_hashes(
            _runtime_evidence_artifact(profile="site", evidence_dir="evidence/runtime-evidence"),
            tmp_path,
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "site",
            "--runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "RUNTIME_EVIDENCE_READY" in result.stdout
    assert "evidence_profile=site" in result.stdout


def test_runtime_evidence_readiness_gate_site_profile_rejects_manifest_without_evidence_hash(tmp_path) -> None:
    evidence_dir = tmp_path / "evidence" / "runtime-evidence"
    _write_runtime_evidence_files(evidence_dir)
    evidence_artifact = _write_json(
        tmp_path / "runtime-evidence-site.json",
        _runtime_evidence_artifact(profile="site", evidence_dir="evidence/runtime-evidence"),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "site",
            "--runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_RUNTIME_EVIDENCE_HASHES" in result.stdout


def test_runtime_evidence_readiness_gate_site_profile_rejects_mismatched_evidence_hash(tmp_path) -> None:
    evidence_dir = tmp_path / "evidence" / "runtime-evidence"
    _write_runtime_evidence_files(evidence_dir)
    artifact = _add_runtime_manifest_hashes(
        _runtime_evidence_artifact(profile="site", evidence_dir="evidence/runtime-evidence"),
        tmp_path,
    )
    _runtime_manifest_entry(artifact, "callback_worker_trace")["evidence_sha256"] = "0" * 64
    evidence_artifact = _write_json(tmp_path / "runtime-evidence-site.json", artifact)

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "site",
            "--runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISMATCHED_RUNTIME_EVIDENCE_HASHES" in result.stdout


def test_runtime_evidence_readiness_gate_production_profile_requires_production_artifacts_after_runtime_evidence(
    tmp_path,
) -> None:
    evidence_dir = tmp_path / "evidence" / "runtime-evidence"
    _write_runtime_evidence_files(evidence_dir)
    evidence_artifact = _write_json(
        tmp_path / "runtime-evidence-production.json",
        _add_runtime_manifest_hashes(
            _runtime_evidence_artifact(profile="production", evidence_dir="evidence/runtime-evidence"),
            tmp_path,
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "production",
            "--runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MISSING_PRODUCTION_CLOSURE_ARTIFACTS" in result.stdout


def test_runtime_evidence_readiness_gate_production_profile_accepts_runtime_and_production_evidence(tmp_path) -> None:
    evidence_dir = tmp_path / "evidence" / "runtime-evidence"
    _write_runtime_evidence_files(evidence_dir)
    runtime_evidence_artifact = _write_json(
        tmp_path / "runtime-evidence-production.json",
        _add_runtime_manifest_hashes(
            _runtime_evidence_artifact(profile="production", evidence_dir="evidence/runtime-evidence"),
            tmp_path,
        ),
    )
    p0_artifact, benchmark_artifact = _write_runtime_production_artifacts(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "production",
            "--runtime-evidence-artifact",
            str(runtime_evidence_artifact),
            "--p0-e2e-artifact",
            str(p0_artifact),
            "--benchmark-artifact",
            str(benchmark_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "RUNTIME_EVIDENCE_READY" in result.stdout
    assert "evidence_profile=production" in result.stdout


def test_runtime_evidence_readiness_gate_rejects_stale_spec_status(tmp_path) -> None:
    _write_minimal_runtime_docs(tmp_path, stale_status=True)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "STALE_RUNTIME_SPEC_STATUS" in result.stdout
    assert "cell-reservation-spec.md" in result.stdout


def test_runtime_evidence_readiness_gate_requires_sorter_preview_capability(tmp_path) -> None:
    _write_minimal_runtime_docs(tmp_path)
    (
        tmp_path / "src" / "app" / "runtime" / "capabilities" / "material_flow" / "sorter_inbound_preview_service.py"
    ).unlink()

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_RUNTIME_READINESS_FILES" in result.stdout
    assert "sorter_inbound_preview_service.py" in result.stdout


def test_runtime_evidence_readiness_gate_requires_reconciliation_preview_capability(tmp_path) -> None:
    _write_minimal_runtime_docs(tmp_path)
    (
        tmp_path
        / "src"
        / "app"
        / "runtime"
        / "capabilities"
        / "material_flow"
        / "smt_ng_wms_reconciliation_preview_service.py"
    ).unlink()

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_RUNTIME_READINESS_FILES" in result.stdout
    assert "smt_ng_wms_reconciliation_preview_service.py" in result.stdout


def test_runtime_evidence_readiness_gate_requires_runtime_capability_files(tmp_path) -> None:
    _write_minimal_runtime_docs(tmp_path)
    (
        tmp_path / "src" / "app" / "runtime" / "capabilities" / "material_flow" / "sorter_inbound_runtime_service.py"
    ).unlink()

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_RUNTIME_READINESS_FILES" in result.stdout
    assert "sorter_inbound_runtime_service.py" in result.stdout
