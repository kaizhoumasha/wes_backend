"""WorkLine restructuring readiness gate contracts."""

from __future__ import annotations

import subprocess
import sys
from importlib import util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_workline_restructuring_readiness_gate.py"
MATRIX_CLOSURE_GUARDRAIL = Path("tests/contracts/test_business_legacy_matrix_closure.py")


def _load_gate_business_scope_contract_tests() -> tuple[Path, ...]:
    spec = util.spec_from_file_location("workline_restructuring_readiness_gate", GATE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return tuple(Path(path) for path in module.BUSINESS_SCOPE_CONTRACT_TESTS)


BUSINESS_SCOPE_CONTRACT_TESTS = _load_gate_business_scope_contract_tests()


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


def _write_business_scope_contract_tests(repo_root: Path) -> None:
    for relative_path in BUSINESS_SCOPE_CONTRACT_TESTS:
        _write(repo_root / relative_path, "def test_material_flow_business_contract_marker():\n    assert True\n")


def _write_minimal_workline_restructuring_repo(repo_root: Path, *, business_ready: bool = False) -> None:
    _write_stub_gate(
        repo_root / "scripts" / "check_runtime_production_closure_gate.py",
        success_token="Runtime production closure mock evidence passed: MOCK_PRODUCTION_CLOSURE",
    )
    _write_stub_gate(
        repo_root / "scripts" / "check_runtime_evidence_readiness_gate.py",
        success_token="Runtime evidence readiness evidence gate passed: RUNTIME_EVIDENCE_READY",
    )
    _write(
        repo_root / "scripts" / "git-quality-gate.sh",
        "run_workline_restructuring_readiness_gate\nworkline-restructuring-readiness\n",
    )
    _write(
        repo_root / "tests" / "architecture" / ("test_" + "phase" + "2_runtime_status_owner_guardrail.py"),
        """PROJECTION_SERVICE = "WorkLineRuntimeStatusProjectionService"
# _direct_runtime_status_writes runtime_status_snapshot


def test_runtime_status_owner_marker():
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
    business_status = "complete" if business_ready else "blocked-until-production-evidence"
    _write(
        repo_root / "docs" / "architecture" / "legacy-cleanup-matrix.md",
        f"""# Legacy Cleanup Matrix

workline_technical_scope_status: complete
workline_business_scope_status: {business_status}

technical scope (`workline-technical`) 通过 check_workline_restructuring_readiness_gate.py --scope technical 后仅删除技术残留。
business scope (`workline-business`) 必须通过 check_workline_restructuring_readiness_gate.py --scope business。
WorkLine 运行态物理字段 target-state cleanup 已完成，由 runtime/orchestration 原生投影承接。
RuntimeInbox callback cutover gate 是两个 scope 的共同前置。
""",
    )
    _write(
        repo_root / "docs" / "architecture" / "workline-and-plugin-restructuring.md",
        """# WorkLine and Plugin Restructuring

WorkLine restructuring readiness gate 使用 check_workline_restructuring_readiness_gate.py。
WorkLine technical scope 允许清理纯技术残留。
WorkLine business scope 必须等待 runtime production closure 与 runtime production evidence profile。
WorkLine 运行态物理字段 target-state cleanup 已完成，由 runtime/orchestration 原生投影承接。
RuntimeInbox callback cutover 已作为删除前共同前置。
        """,
    )
    _write_business_scope_contract_tests(repo_root)


def test_business_contract_list_includes_matrix_closure_guardrail() -> None:
    assert MATRIX_CLOSURE_GUARDRAIL in BUSINESS_SCOPE_CONTRACT_TESTS


def test_workline_restructuring_readiness_gate_technical_scope_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scope", "technical"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "WorkLine restructuring readiness passed: scope=technical" in result.stdout


def test_workline_restructuring_readiness_gate_is_available_from_quality_check() -> None:
    result = subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "scripts" / "git-quality-gate.sh"),
            "--check",
            "workline-restructuring-readiness",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "check_workline_restructuring_readiness_gate.py --scope technical" in result.stdout
    assert "WorkLine restructuring readiness passed: scope=technical" in result.stdout


def test_workline_restructuring_readiness_gate_rejects_workline_runtime_owner_open(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path)
    _write(
        tmp_path / "src" / "app" / "workline" / "services" / "unsafe_owner.py",
        "workline.runtime_status = 'READY'\n",
    )

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scope", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: RUNTIME_STATUS_OWNER_OPEN" in result.stdout
    assert "unsafe_owner.py" in result.stdout


def test_workline_restructuring_readiness_gate_rejects_callback_without_runtime_inbox_cutover(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path)
    _write(
        tmp_path / "src" / "app" / "callback" / "services" / "callback_orchestration_service.py",
        "legacy_workline_inbox_processor\n",
    )

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scope", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: RUNTIME_INBOX_CUTOVER_OPEN" in result.stdout


def test_workline_restructuring_readiness_gate_rejects_missing_technical_behavior_contracts(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scope", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: WORKLINE_TECHNICAL_CONTRACTS_OPEN" in result.stdout
    assert "tests/runtime/orchestration/test_production_closure_evidence_gate.py" in result.stdout


def test_workline_restructuring_readiness_gate_rejects_stale_documents_with_document_reason(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path)
    for relative_path in (
        "tests/runtime/orchestration/test_production_closure_evidence_gate.py",
        "tests/runtime/orchestration/test_runtime_operational_contracts.py",
        "tests/runtime/orchestration/test_runtime_recovery_policies.py",
        "tests/runtime/orchestration/test_runtime_inbox_consumer_service.py",
        "tests/contracts/test_runtime_ops_contract_docs.py",
        "tests/contracts/workline/test_workline_contract_marker.py",
        "tests/characterization/workline_legacy/test_workline_legacy_contract_marker.py",
    ):
        _write(tmp_path / relative_path, "def test_contract_marker():\n    assert True\n")
    _write(
        tmp_path / "docs" / "architecture" / "workline-and-plugin-restructuring.md",
        "WorkLine restructuring readiness gate 使用 check_workline_restructuring_readiness_gate.py，但仍缺 lane 文案。\n",
    )

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scope", "technical", "--repo-root", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: WORKLINE_RESTRUCTURING_DOCUMENTS_OPEN" in result.stdout
    assert "RUNTIME_STATUS_OWNER_OPEN" not in result.stdout


def test_workline_restructuring_readiness_gate_business_scope_requires_runtime_production_closure() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scope", "business"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "WorkLine restructuring readiness failed: MISSING_RUNTIME_PRODUCTION_CLOSURE" in result.stdout


def test_workline_restructuring_readiness_gate_business_scope_requires_runtime_evidence(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path)
    p0_artifact = _write_json(tmp_path / "production-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "runtime-production-benchmark.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--scope",
            "business",
            "--repo-root",
            str(tmp_path),
            "--production-e2e-artifact",
            str(p0_artifact),
            "--runtime-benchmark-artifact",
            str(benchmark_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "WorkLine restructuring readiness failed: MISSING_RUNTIME_PRODUCTION_EVIDENCE" in result.stdout


def test_workline_restructuring_readiness_gate_business_scope_rejects_open_legacy_matrix_items(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path)
    p0_artifact = _write_json(tmp_path / "production-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "runtime-production-benchmark.json")
    runtime_evidence_artifact = _write_json(tmp_path / "runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--scope",
            "business",
            "--repo-root",
            str(tmp_path),
            "--production-e2e-artifact",
            str(p0_artifact),
            "--runtime-benchmark-artifact",
            str(benchmark_artifact),
            "--runtime-evidence-artifact",
            str(runtime_evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: LEGACY_MATRIX_BUSINESS_ITEMS_OPEN" in result.stdout


def test_workline_restructuring_readiness_gate_business_scope_requires_business_contract_tests(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path, business_ready=True)
    (tmp_path / "tests" / "workline_runtime" / "test_runtime_location_event_service.py").unlink()
    p0_artifact = _write_json(tmp_path / "production-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "runtime-production-benchmark.json")
    runtime_evidence_artifact = _write_json(tmp_path / "runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--scope",
            "business",
            "--repo-root",
            str(tmp_path),
            "--production-e2e-artifact",
            str(p0_artifact),
            "--runtime-benchmark-artifact",
            str(benchmark_artifact),
            "--runtime-evidence-artifact",
            str(runtime_evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: WORKLINE_BUSINESS_CONTRACTS_OPEN" in result.stdout
    assert "test_runtime_location_event_service.py" in result.stdout


def test_workline_restructuring_readiness_gate_business_scope_rejects_failing_business_contract_tests(
    tmp_path: Path,
) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path, business_ready=True)
    _write(
        tmp_path / "tests" / "workline_runtime" / "test_runtime_location_event_service.py",
        "def test_material_flow_business_contract_marker():\n    assert False\n",
    )
    p0_artifact = _write_json(tmp_path / "production-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "runtime-production-benchmark.json")
    runtime_evidence_artifact = _write_json(tmp_path / "runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--scope",
            "business",
            "--repo-root",
            str(tmp_path),
            "--production-e2e-artifact",
            str(p0_artifact),
            "--runtime-benchmark-artifact",
            str(benchmark_artifact),
            "--runtime-evidence-artifact",
            str(runtime_evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "WorkLine restructuring readiness failed: WORKLINE_BUSINESS_CONTRACTS_OPEN" in result.stdout
    assert "pytest_exit=1" in result.stdout


def test_workline_restructuring_readiness_gate_business_scope_can_pass_when_matrix_is_closed(tmp_path: Path) -> None:
    _write_minimal_workline_restructuring_repo(tmp_path, business_ready=True)
    p0_artifact = _write_json(tmp_path / "production-e2e.json")
    benchmark_artifact = _write_json(tmp_path / "runtime-production-benchmark.json")
    runtime_evidence_artifact = _write_json(tmp_path / "runtime-evidence.json")

    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--scope",
            "business",
            "--repo-root",
            str(tmp_path),
            "--production-e2e-artifact",
            str(p0_artifact),
            "--runtime-benchmark-artifact",
            str(benchmark_artifact),
            "--runtime-evidence-artifact",
            str(runtime_evidence_artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "WorkLine restructuring readiness passed: scope=business" in result.stdout
