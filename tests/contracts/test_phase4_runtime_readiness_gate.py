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

    status_by_file = {
        "cell-reservation-spec.md": "Phase 4 P0 开发/测试已落地；生产投放热路径未接入",
        "material-location-query-spec.md": "Phase 4 Wave1 开发/测试已落地",
        "workline-active-objects-spec.md": "Phase 4 Wave1 开发/测试已落地",
        "sorter-inbound-capability-spec.md": "Phase 4 本机 MOCK 已验收；生产热路径未接入",
        "smt-ng-wms-reconciliation-spec.md": "Phase 4 本机 MOCK 已验收；生产热路径未接入",
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
Wave2/Wave3 降级为本机开发环境 MOCK 验收，不做生产接入。
开发/测试范围的 Phase4 runtime readiness gate 已关闭。
- [ ] Wave2 生产热路径：production closure profile 与上线确认未通过，未实施。
- [ ] Wave3 生产热路径：Wave2 生产稳定性、production closure profile 与上线确认未通过，未实施。
""",
        encoding="utf-8",
    )
    (repo_root / "docs" / "architecture" / "workline-and-plugin-restructuring.md").write_text(
        """### 10.5 Phase 4: 后续子领域
Wave2/Wave3 降级为本机开发环境 MOCK 验收，不做生产接入。
生产热路径、线上 callback cutover、真实 WMS/ECS effect dispatch 仍受 production closure profile 约束。
- [ ] 分拣机/粗分机入库能力按目标态 capability / port 重建，不保留旧插件兼容入口（生产 runtime 接线未做，保持未勾选）
### 10.6 Phase 5
""",
        encoding="utf-8",
    )
    (repo_root / "tests" / "mock" / "phase4" / "test_wave2_wave3_mock_acceptance.py").write_text(
        '"""本机 MOCK 验收。"""\n',
        encoding="utf-8",
    )
    (repo_root / "tests" / "mock" / "phase4" / "test_sorter_inbound_mock_contracts.py").write_text(
        '"""sorter inbound 本机 MOCK 合同。"""\n',
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
    assert "PHASE4_PRODUCTION_HOT_PATH_NOT_ENABLED" in result.stdout


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
