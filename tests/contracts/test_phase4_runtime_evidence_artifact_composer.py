"""Phase4 runtime evidence artifact composer contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSER_SCRIPT = REPO_ROOT / "scripts" / "compose_phase4_runtime_evidence_artifact.py"
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_phase4_runtime_readiness_gate.py"


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
