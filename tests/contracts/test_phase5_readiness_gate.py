"""Phase 5 readiness gate contracts."""

from __future__ import annotations

import subprocess
import sys
from importlib import util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_phase5_readiness_gate.py"
MATRIX_CLOSURE_GUARDRAIL = Path("tests/contracts/test_phase5_business_lane_matrix_closure.py")


def _load_gate_business_lane_contract_tests() -> tuple[Path, ...]:
    spec = util.spec_from_file_location("phase5_readiness_gate", GATE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return tuple(Path(path) for path in module.BUSINESS_LANE_CONTRACT_TESTS)


BUSINESS_LANE_CONTRACT_TESTS = _load_gate_business_lane_contract_tests()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path: Path) -> Path:
    return _write(path, "{}\n")


def _write_stub_gate(path: Path, *, success_token: str) -> None:
    _write(
        path,
        f"""from __future__ import annotations

import sys

if __name__ == "__main__":
    print("{success_token}")
    raise SystemExit(0)
""",
    )


def _write_business_lane_contract_tests(repo_root: Path) -> None:
    for relative_path in BUSINESS_LANE_CONTRACT_TESTS:
        _write(repo_root / relative_path, "def test_phase4_business_contract_marker():\n    assert True\n")


def _write_minimal_phase5_repo(repo_root: Path, *, business_ready: bool = False) -> None:
    _write_stub_gate(
        repo_root / "scripts" / "check_phase3_closure_gate.py",
        success_token="Phase 3 closure mock evidence passed: MOCK_PHASE3_CLOSURE",
    )
    _write_stub_gate(
        repo_root / "scripts" / "check_phase4_runtime_readiness_gate.py",
        success_token="Phase 4 runtime readiness evidence gate passed: PHASE4_RUNTIME_EVIDENCE_READY",
    )
    _write(
        repo_root / "scripts" / "git-quality-gate.sh",
        "run_phase5_readiness_gate\nphase5-readiness\n",
    )
    _write(
        repo_root / "tests" / "architecture" / "test_phase2_runtime_status_owner_guardrail.py",
        """PROJECTION_SERVICE = "WorkLineRuntimeStatusProjectionService"
# _direct_runtime_status_writes runtime_status_snapshot


def test_phase2_owner_marker():
    assert True
""",
    )
    _write(
        repo_root
        / "src"
        / "app"
        / "runtime"
        / "orchestration"
        / "services"
        / "workline_runtime_status_projection_service.py",
        """class WorkLineRuntimeStatusProjectionService:
    def runtime_status_snapshot(self, workline): ...
    def is_ready(self, workline): ...
    def project_ready(self, workline): workline.runtime_status = "READY"
    def project_stopped_waiting_start(self, workline): workline.runtime_status = "STOPPED"
    def project_reconciling(self, workline): workline.runtime_status = "RECONCILING"
    def project_estopped_active_hold(self, workline): workline.runtime_status = "ESTOPPED"
""",
    )
    _write(
        repo_root / "src" / "app" / "workline" / "services" / "safety_service.py",
        "snapshot = projection.runtime_status_snapshot(workline)\nstatus = snapshot.runtime_status\n",
    )
    _write(
        repo_root / "tests" / "callback" / "test_callback_runtime_inbox_cutover.py",
        """def test_process_result_writes_runtime_inbox_before_legacy_workline_inbox():
    assert True


def test_process_event_writes_runtime_inbox_before_legacy_workline_inbox():
    assert True


def test_process_external_writes_runtime_inbox_before_legacy_transition_delegate():
    assert True


def test_process_result_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources():
    assert True
""",
    )
    _write(
        repo_root / "src" / "app" / "runtime" / "orchestration" / "consumers" / "callback_runtime_inbox_writer.py",
        """class CallbackRuntimeInboxWriter:
    async def write_result_callback(self): return await self.service.accept_received()
    async def write_event_callback(self): return await self.service.accept_received()
    async def write_external_callback(self): return await self.service.accept_received()
callback_runtime_inbox_writer = CallbackRuntimeInboxWriter()
""",
    )
    _write(
        repo_root / "src" / "app" / "callback" / "services" / "callback_orchestration_service.py",
        "callback_runtime_inbox_writer\nwrite_result_callback\nwrite_event_callback\nwrite_external_callback\n",
    )
    business_status = "final-cleanup-complete" if business_ready else "blocked-until-production-evidence"
    _write(
        repo_root / "docs" / "architecture" / "legacy-cleanup-matrix.md",
        f"""# Legacy Cleanup Matrix

phase5_technical_lane_status: final-cleanup-complete
phase5_business_lane_status: {business_status}

technical lane (`phase5-tech`) 通过 check_phase5_readiness_gate.py --lane technical 后仅删除技术残留。
business lane (`phase5-business`) 必须通过 check_phase5_readiness_gate.py --lane business。
WorkLine 运行态物理字段 final cleanup 已完成，由 runtime/orchestration 原生投影承接。
RuntimeInbox callback cutover gate 是两个 lane 的共同前置。
""",
    )
    _write(
        repo_root / "docs" / "architecture" / "workline-and-plugin-restructuring.md",
        """# WorkLine and Plugin Restructuring

Phase5 readiness gate 使用 check_phase5_readiness_gate.py。
Phase5 technical lane 允许清理纯技术残留。
Phase5 business lane 必须等待 Phase3 production closure 与 Phase4 production evidence profile。
WorkLine 运行态物理字段 final cleanup 已完成，由 runtime/orchestration 原生投影承接。
RuntimeInbox callback cutover 已作为删除前共同前置。
        """,
    )
    _write_business_lane_contract_tests(repo_root)


def test_phase5_business_contract_list_includes_matrix_closure_guardrail() -> None:
    assert MATRIX_CLOSURE_GUARDRAIL in BUSINESS_LANE_CONTRACT_TESTS


def test_phase5_readiness_gate_technical_lane_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--lane", "technical"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Phase 5 readiness passed: lane=technical" in result.stdout


def test_phase5_readiness_gate_is_available_from_quality_check() -> None:
    result = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "scripts" / "git-quality-gate.sh"), "--check", "phase5-readiness"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "check_phase5_readiness_gate.py --lane technical" in result.stdout
    assert "Phase 5 readiness passed: lane=technical" in result.stdout


def test_phase5_readiness_gate_rejects_workline_runtime_owner_open(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path)
    _write(
        tmp_path / "src" / "app" / "workline" / "services" / "unsafe_owner.py",
        "workline.runtime_status = 'READY'\n",
    )

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--lane", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: PHASE2_RUNTIME_STATUS_OWNER_OPEN" in result.stdout
    assert "unsafe_owner.py" in result.stdout


def test_phase5_readiness_gate_rejects_callback_without_runtime_inbox_cutover(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path)
    _write(
        tmp_path / "src" / "app" / "callback" / "services" / "callback_orchestration_service.py",
        "legacy_workline_inbox_processor\n",
    )

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--lane", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: RUNTIME_INBOX_CUTOVER_OPEN" in result.stdout


def test_phase5_readiness_gate_rejects_missing_technical_behavior_contracts(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--lane", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: PHASE5_TECHNICAL_CONTRACTS_OPEN" in result.stdout
    assert "tests/runtime/orchestration/test_phase3_closure_evidence_gate.py" in result.stdout


def test_phase5_readiness_gate_rejects_stale_phase5_documents_with_document_reason(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path)
    for relative_path in (
        "tests/runtime/orchestration/test_phase3_closure_evidence_gate.py",
        "tests/runtime/orchestration/test_phase3_operational_contracts.py",
        "tests/runtime/orchestration/test_phase3_recovery_policies.py",
        "tests/runtime/orchestration/test_runtime_inbox_phase3_service.py",
        "tests/contracts/test_phase3_ops_contract_docs.py",
        "tests/contracts/workline/test_workline_contract_marker.py",
        "tests/characterization/workline_legacy/test_workline_legacy_contract_marker.py",
    ):
        _write(tmp_path / relative_path, "def test_contract_marker():\n    assert True\n")
    _write(
        tmp_path / "docs" / "architecture" / "workline-and-plugin-restructuring.md",
        "Phase5 readiness gate 使用 check_phase5_readiness_gate.py，但仍缺 lane 文案。\n",
    )

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--lane", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: PHASE5_READINESS_DOCUMENTS_OPEN" in result.stdout
    assert "PHASE2_RUNTIME_STATUS_OWNER_OPEN" not in result.stdout


def test_phase5_readiness_gate_business_lane_requires_phase3_production_closure() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--lane", "business"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Phase 5 readiness failed: MISSING_PHASE3_PRODUCTION_CLOSURE" in result.stdout


def test_phase5_readiness_gate_business_lane_requires_phase4_production_evidence(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path)
    p0_artifact = _write_json(tmp_path / "phase3-p0-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "phase3-production-benchmark.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--lane",
            "business",
            "--repo-root",
            str(tmp_path),
            "--phase3-p0-e2e-artifact",
            str(p0_artifact),
            "--phase3-benchmark-artifact",
            str(benchmark_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Phase 5 readiness failed: MISSING_PHASE4_PRODUCTION_EVIDENCE" in result.stdout


def test_phase5_readiness_gate_business_lane_rejects_open_legacy_matrix_items(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path)
    p0_artifact = _write_json(tmp_path / "phase3-p0-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "phase3-production-benchmark.json")
    phase4_artifact = _write_json(tmp_path / "phase4-runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--lane",
            "business",
            "--repo-root",
            str(tmp_path),
            "--phase3-p0-e2e-artifact",
            str(p0_artifact),
            "--phase3-benchmark-artifact",
            str(benchmark_artifact),
            "--phase4-evidence-artifact",
            str(phase4_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN" in result.stdout


def test_phase5_readiness_gate_business_lane_requires_phase4_contract_tests(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path, business_ready=True)
    (tmp_path / "tests" / "workline_runtime" / "test_runtime_location_event_service.py").unlink()
    p0_artifact = _write_json(tmp_path / "phase3-p0-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "phase3-production-benchmark.json")
    phase4_artifact = _write_json(tmp_path / "phase4-runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--lane",
            "business",
            "--repo-root",
            str(tmp_path),
            "--phase3-p0-e2e-artifact",
            str(p0_artifact),
            "--phase3-benchmark-artifact",
            str(benchmark_artifact),
            "--phase4-evidence-artifact",
            str(phase4_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: PHASE5_BUSINESS_CONTRACTS_OPEN" in result.stdout
    assert "test_runtime_location_event_service.py" in result.stdout


def test_phase5_readiness_gate_business_lane_rejects_failing_phase4_contract_tests(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path, business_ready=True)
    _write(
        tmp_path / "tests" / "workline_runtime" / "test_runtime_location_event_service.py",
        "def test_phase4_business_contract_marker():\n    assert False\n",
    )
    p0_artifact = _write_json(tmp_path / "phase3-p0-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "phase3-production-benchmark.json")
    phase4_artifact = _write_json(tmp_path / "phase4-runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--lane",
            "business",
            "--repo-root",
            str(tmp_path),
            "--phase3-p0-e2e-artifact",
            str(p0_artifact),
            "--phase3-benchmark-artifact",
            str(benchmark_artifact),
            "--phase4-evidence-artifact",
            str(phase4_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Phase 5 readiness failed: PHASE5_BUSINESS_CONTRACTS_OPEN" in result.stdout
    assert "pytest_exit=1" in result.stdout


def test_phase5_readiness_gate_business_lane_can_pass_when_matrix_is_closed(tmp_path: Path) -> None:
    _write_minimal_phase5_repo(tmp_path, business_ready=True)
    p0_artifact = _write_json(tmp_path / "phase3-p0-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "phase3-production-benchmark.json")
    phase4_artifact = _write_json(tmp_path / "phase4-runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--lane",
            "business",
            "--repo-root",
            str(tmp_path),
            "--phase3-p0-e2e-artifact",
            str(p0_artifact),
            "--phase3-benchmark-artifact",
            str(benchmark_artifact),
            "--phase4-evidence-artifact",
            str(phase4_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Phase 5 readiness passed: lane=business" in result.stdout
