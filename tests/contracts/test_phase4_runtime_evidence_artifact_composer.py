"""Phase4 runtime evidence artifact composer contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSER_SCRIPT = REPO_ROOT / "scripts" / "compose_phase4_runtime_evidence_artifact.py"
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_phase4_runtime_readiness_gate.py"


def _write_phase4_evidence_files(base_dir: Path) -> None:
    for relative_path in (
        "provider-contracts/sorter-inbound.json",
        "provider-contracts/smt-ng-wms-reconciliation.json",
        "traces/effect-dispatch.json",
        "traces/runtime-inbox-worker.json",
        "traces/runtime-hold-reconciliation.json",
        "benchmarks/phase4-runtime.json",
    ):
        evidence_path = base_dir / relative_path
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps({"evidence": relative_path, "result": "PASS"}), encoding="utf-8")


def test_phase4_runtime_evidence_composer_writes_gate_accepted_simulator_artifact(tmp_path) -> None:
    artifact_path = tmp_path / "phase4-runtime-evidence-simulator.json"

    compose_result = subprocess.run(
        [
            sys.executable,
            str(COMPOSER_SCRIPT),
            "--output",
            str(artifact_path),
            "--profile",
            "simulator",
            "--environment",
            "local-wms-ecs-simulator",
            "--generated-at",
            "2026-07-05T00:00:00Z",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert compose_result.returncode == 0, compose_result.stderr + compose_result.stdout
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["profile"] == {
        "name": "simulator",
        "environment": "local-wms-ecs-simulator",
        "generated_at": "2026-07-05T00:00:00Z",
    }
    assert artifact["capabilities"] == ["sorter_inbound", "smt_ng_wms_reconciliation"]

    gate_result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "simulator",
            "--phase4-runtime-evidence-artifact",
            str(artifact_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert gate_result.returncode == 0
    assert "PHASE4_RUNTIME_EVIDENCE_READY" in gate_result.stdout


def test_phase4_runtime_evidence_composer_requires_evidence_dir_for_site_profile(tmp_path) -> None:
    artifact_path = tmp_path / "phase4-runtime-evidence-site.json"

    compose_result = subprocess.run(
        [
            sys.executable,
            str(COMPOSER_SCRIPT),
            "--output",
            str(artifact_path),
            "--profile",
            "site",
            "--environment",
            "field-dry-run",
            "--generated-at",
            "2026-07-05T00:00:00Z",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert compose_result.returncode == 1
    assert "Phase4 site/production evidence-dir is required" in compose_result.stdout


def test_phase4_runtime_evidence_composer_writes_gate_accepted_site_artifact(tmp_path) -> None:
    artifact_path = tmp_path / "phase4-runtime-evidence-site.json"
    evidence_dir = tmp_path / "evidence" / "phase4-runtime"
    _write_phase4_evidence_files(evidence_dir)

    compose_result = subprocess.run(
        [
            sys.executable,
            str(COMPOSER_SCRIPT),
            "--output",
            str(artifact_path),
            "--profile",
            "site",
            "--environment",
            "field-dry-run",
            "--generated-at",
            "2026-07-05T00:00:00Z",
            "--evidence-dir",
            "evidence/phase4-runtime",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert compose_result.returncode == 0, compose_result.stderr + compose_result.stdout
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["profile"]["name"] == "site"
    assert artifact["evidence_manifest"]["provider_contracts"]["sorter_inbound"] == {
        "kind": "provider-contract",
        "evidence": "evidence/phase4-runtime/provider-contracts/sorter-inbound.json",
    }
    assert artifact["evidence_manifest"]["callback_worker_trace"]["evidence"] == (
        "evidence/phase4-runtime/traces/runtime-inbox-worker.json"
    )

    gate_result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--readiness-profile",
            "site",
            "--phase4-runtime-evidence-artifact",
            str(artifact_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert gate_result.returncode == 0
    assert "PHASE4_RUNTIME_EVIDENCE_READY" in gate_result.stdout
    assert "evidence_profile=site" in gate_result.stdout
