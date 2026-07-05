"""Phase 4 runtime readiness gate contracts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_phase4_runtime_readiness_gate.py"


def _write_minimal_phase4_docs(repo_root: Path, *, stale_status: bool = False) -> None:
    docs_architecture = repo_root / "docs" / "architecture"
    docs_architecture.mkdir(parents=True, exist_ok=True)
    (repo_root / "docs" / "superpowers" / "plans").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests" / "mock" / "phase4").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests" / "workline_runtime").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "app" / "runtime" / "capabilities" / "phase4").mkdir(parents=True, exist_ok=True)

    status_by_file = {
        "cell-reservation-spec.md": "Phase 4 P0 开发/测试已落地；生产投放热路径未接入",
        "material-location-query-spec.md": "Phase 4 Wave1 开发/测试已落地",
        "workline-active-objects-spec.md": "Phase 4 Wave1 开发/测试已落地",
        "sorter-inbound-capability-spec.md": "Phase 4 runtime capability 已落地；evidence profile 未闭合",
        "smt-ng-wms-reconciliation-spec.md": "Phase 4 runtime capability 已落地；evidence profile 未闭合",
    }
    if stale_status:
        status_by_file["cell-reservation-spec.md"] = "Phase 4 P0 前置设计 SPEC，未实现"

    for filename, status in status_by_file.items():
        (docs_architecture / filename).write_text(
            f"# {filename}\n\n> 状态：{status}\n> 父计划：mock\n",
            encoding="utf-8",
        )

    (repo_root / "docs" / "superpowers" / "plans" / "2026-07-04-phase4-runtime-readiness.md").write_text(
        """# Phase4 Runtime Readiness 实施计划
Wave2/Wave3 后续目标是 production-capable runtime path，外部 provider 可替换。
开发/测试范围的 Phase4 runtime readiness gate 已关闭。
- [x] Wave2 runtime capability builder：已输出 RuntimeIntent/effect contract/evidence。
- [x] Wave3 runtime capability builder：已输出 RuntimeIntent/RuntimeInbox evidence/RuntimeHold plan。
- [ ] Wave2 evidence profile：provider contract 证据未提供。
- [ ] Wave3 evidence profile：provider contract 证据未提供。
""",
        encoding="utf-8",
    )
    (repo_root / "docs" / "architecture" / "workline-and-plugin-restructuring.md").write_text(
        """### 10.5 Phase 4: 后续子领域
Wave2/Wave3 后续目标是 production-capable runtime path，外部 provider 可替换。
evidence profile 只改变验收证据要求，不改变 service 行为。
- [x] 分拣机/粗分机入库能力 runtime capability builder 已按目标态 capability / port 重建，不保留旧插件兼容入口
### 10.6 Phase 5
""",
        encoding="utf-8",
    )
    (repo_root / "tests" / "mock" / "phase4" / "test_wave2_wave3_mock_acceptance.py").write_text(
        '"""本机 MOCK 验收，不代表 evidence profile 闭合。"""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "mock" / "phase4" / "test_sorter_inbound_mock_contracts.py").write_text(
        '"""sorter inbound 本机 MOCK 合同，不代表 evidence profile 闭合。"""\n',
        encoding="utf-8",
    )
    (
        repo_root / "src" / "app" / "runtime" / "capabilities" / "phase4" / "sorter_inbound_preview_service.py"
    ).write_text(
        '"""Phase4 sorter inbound preview capability; LOCAL_MOCK_ONLY; production_write_path; legacy_plugin_entry_used."""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_sorter_inbound_preview_service.py").write_text(
        (
            '"""Phase4 sorter inbound preview capability 合同。"""\n'
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
        / "phase4"
        / "smt_ng_wms_reconciliation_preview_service.py"
    ).write_text(
        '"""Phase4 SMT/NG/WMS preview; LOCAL_MOCK_ONLY; production_write_path; legacy_plugin_entry_used; RuntimeHold."""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_smt_ng_wms_reconciliation_preview_service.py").write_text(
        (
            '"""Phase4 SMT/NG/WMS preview capability 合同。"""\n'
            '"production_write_path legacy_plugin_entry_used IDEMPOTENT_DUPLICATE RuntimeHold scope-only"\n'
        ),
        encoding="utf-8",
    )
    (
        repo_root / "src" / "app" / "runtime" / "capabilities" / "phase4" / "sorter_inbound_runtime_service.py"
    ).write_text(
        (
            '"""Phase4 sorter inbound runtime capability."""\n'
            '"RuntimeIntent WmsFulfillmentPort.notify_pkg_binding '
            'WmsInventoryTransactionPort.confirm_inbound provider-contract"\n'
        ),
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_sorter_inbound_runtime_service.py").write_text(
        (
            '"""Phase4 sorter inbound runtime capability 合同。"""\n'
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
        / "phase4"
        / "smt_ng_wms_reconciliation_runtime_service.py"
    ).write_text(
        '"""Phase4 SMT/NG/WMS reconciliation runtime capability; RuntimeIntent RuntimeInbox provider-contract."""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "workline_runtime" / "test_smt_ng_wms_reconciliation_runtime_service.py").write_text(
        '"""Phase4 SMT/NG/WMS reconciliation runtime capability 合同。 RuntimeIntent RuntimeInbox provider-contract."""\n',
        encoding="utf-8",
    )


def test_phase4_runtime_readiness_gate_dev_mock_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "MOCK_PHASE4_RUNTIME_READINESS" in result.stdout


def test_phase4_runtime_readiness_gate_production_profile_is_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--readiness-profile", "production"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MISSING_PHASE4_RUNTIME_EVIDENCE_ARTIFACT" in result.stdout
    assert "evidence_profile=production" in result.stdout


def test_phase4_runtime_readiness_gate_simulator_profile_requires_evidence_not_code_branch() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--readiness-profile", "simulator"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MISSING_PHASE4_RUNTIME_EVIDENCE_ARTIFACT" in result.stdout
    assert "evidence_profile=simulator" in result.stdout


def test_phase4_runtime_readiness_gate_simulator_profile_accepts_provider_contract_evidence(tmp_path) -> None:
    evidence_artifact = tmp_path / "phase4-runtime-evidence.json"
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
            "--phase4-runtime-evidence-artifact",
            str(evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "PHASE4_RUNTIME_EVIDENCE_READY" in result.stdout
    assert "evidence_profile=simulator" in result.stdout


def test_phase4_runtime_readiness_gate_rejects_stale_spec_status(tmp_path) -> None:
    _write_minimal_phase4_docs(tmp_path, stale_status=True)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "STALE_PHASE4_SPEC_STATUS" in result.stdout
    assert "cell-reservation-spec.md" in result.stdout


def test_phase4_runtime_readiness_gate_requires_sorter_preview_capability(tmp_path) -> None:
    _write_minimal_phase4_docs(tmp_path)
    (tmp_path / "src" / "app" / "runtime" / "capabilities" / "phase4" / "sorter_inbound_preview_service.py").unlink()

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_PHASE4_READINESS_FILES" in result.stdout
    assert "sorter_inbound_preview_service.py" in result.stdout


def test_phase4_runtime_readiness_gate_requires_reconciliation_preview_capability(tmp_path) -> None:
    _write_minimal_phase4_docs(tmp_path)
    (
        tmp_path
        / "src"
        / "app"
        / "runtime"
        / "capabilities"
        / "phase4"
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
    assert "MISSING_PHASE4_READINESS_FILES" in result.stdout
    assert "smt_ng_wms_reconciliation_preview_service.py" in result.stdout


def test_phase4_runtime_readiness_gate_requires_runtime_capability_files(tmp_path) -> None:
    _write_minimal_phase4_docs(tmp_path)
    (tmp_path / "src" / "app" / "runtime" / "capabilities" / "phase4" / "sorter_inbound_runtime_service.py").unlink()

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING_PHASE4_READINESS_FILES" in result.stdout
    assert "sorter_inbound_runtime_service.py" in result.stdout
