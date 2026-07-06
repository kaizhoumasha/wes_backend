"""Validate Phase 5 legacy cleanup readiness lanes."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


PHASE2_RUNTIME_STATUS_OWNER_OPEN = "PHASE2_RUNTIME_STATUS_OWNER_OPEN"
RUNTIME_INBOX_CUTOVER_OPEN = "RUNTIME_INBOX_CUTOVER_OPEN"
PHASE3_MOCK_CLOSURE_OPEN = "PHASE3_MOCK_CLOSURE_OPEN"
PHASE5_TECHNICAL_CONTRACTS_OPEN = "PHASE5_TECHNICAL_CONTRACTS_OPEN"
PHASE5_BUSINESS_CONTRACTS_OPEN = "PHASE5_BUSINESS_CONTRACTS_OPEN"
PHASE5_READINESS_DOCUMENTS_OPEN = "PHASE5_READINESS_DOCUMENTS_OPEN"
MISSING_PHASE3_PRODUCTION_CLOSURE = "MISSING_PHASE3_PRODUCTION_CLOSURE"
MISSING_PHASE4_PRODUCTION_EVIDENCE = "MISSING_PHASE4_PRODUCTION_EVIDENCE"
LEGACY_MATRIX_BUSINESS_ITEMS_OPEN = "LEGACY_MATRIX_BUSINESS_ITEMS_OPEN"

PROJECTION_SERVICE = Path("src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py")
OWNER_SENSITIVE_ROOTS = (
    Path("src/app/workline"),
    Path("src/app/runtime/capabilities/phase4"),
)
DOC_PATHS = (
    Path("docs/architecture/workline-and-plugin-restructuring.md"),
    Path("docs/architecture/legacy-cleanup-matrix.md"),
)
PHASE2_GUARDRAIL_TEST = Path("tests/architecture/test_phase2_runtime_status_owner_guardrail.py")
PHASE3_CLOSURE_SCRIPT = Path("scripts/check_phase3_closure_gate.py")
PHASE4_READINESS_SCRIPT = Path("scripts/check_phase4_runtime_readiness_gate.py")
LEGACY_MATRIX = Path("docs/architecture/legacy-cleanup-matrix.md")
MAIN_PLAN = Path("docs/architecture/workline-and-plugin-restructuring.md")
TECHNICAL_LANE_CONTRACT_TESTS = (
    Path("tests/architecture/test_phase2_runtime_status_owner_guardrail.py"),
    Path("tests/callback/test_callback_runtime_inbox_cutover.py"),
    Path("tests/runtime/orchestration/test_phase3_closure_evidence_gate.py"),
    Path("tests/runtime/orchestration/test_phase3_operational_contracts.py"),
    Path("tests/runtime/orchestration/test_phase3_recovery_policies.py"),
    Path("tests/runtime/orchestration/test_runtime_inbox_phase3_service.py"),
    Path("tests/contracts/test_phase3_ops_contract_docs.py"),
    Path("tests/contracts/workline"),
    Path("tests/characterization/workline_legacy"),
)
BUSINESS_LANE_CONTRACT_TESTS = (
    Path("tests/contracts/test_phase4_design_docs.py"),
    Path("tests/contracts/test_phase4_runtime_readiness_gate.py"),
    Path("tests/contracts/test_phase4_runtime_evidence_artifact_composer.py"),
    Path("tests/mock/phase4/test_sorter_inbound_mock_contracts.py"),
    Path("tests/mock/phase4/test_wave2_wave3_mock_acceptance.py"),
    Path("tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py"),
    Path("tests/workline_runtime/test_runtime_location_event_service.py"),
    Path("tests/workline_runtime/test_material_location_query_service.py"),
    Path("tests/workline_runtime/test_workline_active_objects_service.py"),
    Path("tests/workline_runtime/test_sorter_inbound_preview_service.py"),
    Path("tests/workline_runtime/test_sorter_inbound_runtime_service.py"),
    Path("tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py"),
    Path("tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py"),
    Path("tests/api/test_phase4_read_model_routes.py"),
    Path("tests/migrations/test_phase4_runtime_location_reservation_migration.py"),
)


@dataclass(frozen=True)
class GateResult:
    valid: bool
    reason: str
    details: tuple[str, ...] = ()
    exit_code: int = 1


def _read(repo_root: Path, relative_path: Path) -> str | None:
    try:
        return (repo_root / relative_path).read_text(encoding="utf-8")
    except OSError:
        return None


def _missing_files(repo_root: Path, relative_paths: Iterable[Path]) -> tuple[str, ...]:
    return tuple(str(path) for path in relative_paths if not (repo_root / path).exists())


def _missing_tokens(text: str | None, required_tokens: Iterable[str], *, source: Path) -> tuple[str, ...]:
    if text is None:
        return (str(source),)
    return tuple(f"{source}:{token}" for token in required_tokens if token not in text)


def _parse_source(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _is_runtime_status_attr(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "runtime_status"


def _target_contains_runtime_status_attr(target: ast.AST) -> bool:
    return any(_is_runtime_status_attr(node) for node in ast.walk(target))


def _is_runtime_status_setattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "runtime_status"
    )


def _dict_has_runtime_status_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == "runtime_status" for key in node.keys
    )


def _values_call_writes_runtime_status(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "values"
        and (
            any(keyword.arg == "runtime_status" for keyword in node.keywords)
            or any(_dict_has_runtime_status_key(arg) for arg in node.args)
        )
    )


def _direct_runtime_status_writes(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = [node.target] if isinstance(node, ast.AnnAssign | ast.AugAssign) else list(node.targets)
            if any(_target_contains_runtime_status_attr(target) for target in targets):
                lines.append(node.lineno)
        elif _is_runtime_status_setattr(node) or _values_call_writes_runtime_status(node):
            lines.append(node.lineno)
    return sorted(set(lines))


def _is_runtime_status_snapshot_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "runtime_status_snapshot")
        or (isinstance(node.func, ast.Name) and node.func.id == "runtime_status_snapshot")
    )


def _runtime_status_snapshot_vars(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_runtime_status_snapshot_call(node.value):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_runtime_status_snapshot_call(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _is_projection_snapshot_attr(node: ast.Attribute, snapshot_vars: set[str]) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id in snapshot_vars


def _is_runtime_status_getattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "runtime_status"
    )


def _direct_runtime_status_reads(tree: ast.AST) -> list[int]:
    snapshot_vars = _runtime_status_snapshot_vars(tree)
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if _is_runtime_status_getattr(node)
            or (
                _is_runtime_status_attr(node)
                and not isinstance(node.ctx, ast.Store)
                and not _is_projection_snapshot_attr(node, snapshot_vars)
            )
        }
    )


def _iter_python_files(repo_root: Path, roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        absolute_root = repo_root / root
        if absolute_root.exists():
            yield from sorted(root / path.relative_to(absolute_root) for path in absolute_root.rglob("*.py"))


def _runtime_status_owner_failures(repo_root: Path) -> tuple[str, ...]:
    failures: list[str] = []
    for rel_path in sorted((repo_root / "src" / "app").rglob("*.py")):
        relative_path = rel_path.relative_to(repo_root)
        if relative_path == PROJECTION_SERVICE:
            continue
        tree = _parse_source(rel_path)
        if tree is None:
            failures.append(f"{relative_path}:unparseable")
            continue
        failures.extend(f"{relative_path}:{line}" for line in _direct_runtime_status_writes(tree))

    for relative_path in _iter_python_files(repo_root, OWNER_SENSITIVE_ROOTS):
        if relative_path == PROJECTION_SERVICE:
            continue
        tree = _parse_source(repo_root / relative_path)
        if tree is None:
            failures.append(f"{relative_path}:unparseable")
            continue
        failures.extend(f"{relative_path}:{line}" for line in _direct_runtime_status_reads(tree))
    return tuple(sorted(set(failures)))


def _phase2_owner_result(repo_root: Path) -> GateResult:
    missing = _missing_files(repo_root, (PHASE2_GUARDRAIL_TEST, PROJECTION_SERVICE, *DOC_PATHS))
    if missing:
        return GateResult(False, PHASE2_RUNTIME_STATUS_OWNER_OPEN, missing)

    guardrail_test = _read(repo_root, PHASE2_GUARDRAIL_TEST)
    projection_service = _read(repo_root, PROJECTION_SERVICE)
    missing_tokens = (
        *_missing_tokens(
            guardrail_test,
            (
                "PROJECTION_SERVICE",
                "_direct_runtime_status_writes",
                "runtime_status_snapshot",
                "WorkLineRuntimeStatusProjectionService",
            ),
            source=PHASE2_GUARDRAIL_TEST,
        ),
        *_missing_tokens(
            projection_service,
            (
                "class WorkLineRuntimeStatusProjectionService",
                "runtime_status_snapshot",
                "project_ready",
                "project_stopped_waiting_start",
                "project_reconciling",
                "project_estopped_active_hold",
            ),
            source=PROJECTION_SERVICE,
        ),
    )
    if missing_tokens:
        return GateResult(False, PHASE2_RUNTIME_STATUS_OWNER_OPEN, missing_tokens)

    forbidden_phrases = (
        "WorkLine.runtime_status 状态 owner",
        "WorkLine.runtime_status 运行状态 owner",
        "WorkLine.runtime_status owner",
        "WorkLine.runtime_status 事实源",
        "WorkLine.runtime_status 权威",
        "runtime_status 状态 owner",
        "runtime_status 运行状态 owner",
        "runtime_status owner",
    )
    doc_violations: list[str] = []
    for doc_path in DOC_PATHS:
        text = _read(repo_root, doc_path) or ""
        doc_violations.extend(f"{doc_path}:{phrase}" for phrase in forbidden_phrases if phrase in text)
    owner_violations = _runtime_status_owner_failures(repo_root)
    if doc_violations or owner_violations:
        return GateResult(False, PHASE2_RUNTIME_STATUS_OWNER_OPEN, (*doc_violations, *owner_violations))

    return GateResult(True, "PHASE2_RUNTIME_STATUS_OWNER_CLOSED")


def _runtime_inbox_cutover_result(repo_root: Path) -> GateResult:
    writer = Path("src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py")
    orchestration = Path("src/app/callback/services/callback_orchestration_service.py")
    cutover_test = Path("tests/callback/test_callback_runtime_inbox_cutover.py")
    missing = _missing_files(repo_root, (writer, orchestration, cutover_test))
    if missing:
        return GateResult(False, RUNTIME_INBOX_CUTOVER_OPEN, missing)

    writer_text = _read(repo_root, writer)
    orchestration_text = _read(repo_root, orchestration)
    test_text = _read(repo_root, cutover_test)
    missing_tokens = (
        *_missing_tokens(
            writer_text,
            (
                "CallbackRuntimeInboxWriter",
                "accept_received",
                "write_result_callback",
                "write_event_callback",
                "write_external_callback",
            ),
            source=writer,
        ),
        *_missing_tokens(
            orchestration_text,
            (
                "callback_runtime_inbox_writer",
                "write_result_callback",
                "write_event_callback",
                "write_external_callback",
            ),
            source=orchestration,
        ),
        *_missing_tokens(
            test_text,
            (
                "test_process_result_writes_runtime_inbox_before_legacy_workline_inbox",
                "test_process_event_writes_runtime_inbox_before_legacy_workline_inbox",
                "test_process_external_writes_runtime_inbox_before_legacy_transition_delegate",
                "test_process_result_duplicate_uses_runtime_inbox_ack_and_skips_legacy_sources",
            ),
            source=cutover_test,
        ),
    )
    if missing_tokens:
        return GateResult(False, RUNTIME_INBOX_CUTOVER_OPEN, missing_tokens)
    return GateResult(True, "RUNTIME_INBOX_CUTOVER_CLOSED")


def _run_gate(repo_root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - args only invoke repository-owned gate scripts with explicit paths.
        [sys.executable, *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _phase3_mock_closure_result(repo_root: Path) -> GateResult:
    if not (repo_root / PHASE3_CLOSURE_SCRIPT).exists():
        return GateResult(False, PHASE3_MOCK_CLOSURE_OPEN, (str(PHASE3_CLOSURE_SCRIPT),))
    result = _run_gate(repo_root, [str(repo_root / PHASE3_CLOSURE_SCRIPT)])
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or ("MOCK_PHASE3_CLOSURE" not in output and "mock evidence passed" not in output):
        return GateResult(False, PHASE3_MOCK_CLOSURE_OPEN, (output.strip(),))
    return GateResult(True, "PHASE3_MOCK_CLOSURE_CLOSED")


def _pytest_contracts_result(repo_root: Path, contract_tests: Sequence[Path], *, failure_reason: str) -> GateResult:
    missing = _missing_files(repo_root, contract_tests)
    if missing:
        return GateResult(False, failure_reason, missing)

    result = subprocess.run(  # noqa: S603 - fixed pytest module plus repository-owned test paths.
        [sys.executable, "-m", "pytest", *(str(path) for path in contract_tests), "-q"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = tuple(
            detail
            for detail in (
                f"pytest_exit={result.returncode}",
                _tail(result.stdout),
                _tail(result.stderr),
            )
            if detail
        )
        return GateResult(False, failure_reason, details)
    return GateResult(True, f"{failure_reason.removesuffix('_OPEN')}_CLOSED")


def _technical_contracts_result(repo_root: Path) -> GateResult:
    return _pytest_contracts_result(
        repo_root,
        TECHNICAL_LANE_CONTRACT_TESTS,
        failure_reason=PHASE5_TECHNICAL_CONTRACTS_OPEN,
    )


def _business_contracts_result(repo_root: Path) -> GateResult:
    return _pytest_contracts_result(
        repo_root,
        BUSINESS_LANE_CONTRACT_TESTS,
        failure_reason=PHASE5_BUSINESS_CONTRACTS_OPEN,
    )


def _tail(text: str, *, max_lines: int = 12) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def _document_readiness_result(repo_root: Path, *, lane: str) -> GateResult:
    missing = _missing_files(repo_root, (LEGACY_MATRIX, MAIN_PLAN))
    if missing:
        return GateResult(False, PHASE5_READINESS_DOCUMENTS_OPEN, missing)
    legacy_matrix = _read(repo_root, LEGACY_MATRIX) or ""
    main_plan = _read(repo_root, MAIN_PLAN) or ""
    required_common_tokens = (
        "check_phase5_readiness_gate.py",
        "technical lane",
        "business lane",
    )
    missing_tokens = [f"{LEGACY_MATRIX}:{token}" for token in required_common_tokens if token not in legacy_matrix]
    missing_tokens.extend(f"{MAIN_PLAN}:{token}" for token in required_common_tokens if token not in main_plan)
    if lane == "technical":
        missing_tokens.extend(
            f"{LEGACY_MATRIX}:{token}"
            for token in ("phase5_technical_lane_status: ready-for-technical-cleanup", "phase5-tech")
            if token not in legacy_matrix
        )
        if missing_tokens:
            return GateResult(False, PHASE5_READINESS_DOCUMENTS_OPEN, tuple(missing_tokens))
        return GateResult(True, "PHASE5_TECHNICAL_MATRIX_READY")

    if "phase5_business_lane_status: ready-for-business-cleanup" not in legacy_matrix:
        return GateResult(False, LEGACY_MATRIX_BUSINESS_ITEMS_OPEN, ("phase5_business_lane_status",))
    if missing_tokens:
        return GateResult(False, PHASE5_READINESS_DOCUMENTS_OPEN, tuple(missing_tokens))
    return GateResult(True, "PHASE5_BUSINESS_MATRIX_READY")


def _phase3_production_result(
    repo_root: Path,
    *,
    p0_e2e_artifact: str | None,
    benchmark_artifact: str | None,
) -> GateResult:
    if not p0_e2e_artifact or not benchmark_artifact:
        missing = []
        if not p0_e2e_artifact:
            missing.append("phase3-p0-e2e-artifact")
        if not benchmark_artifact:
            missing.append("phase3-benchmark-artifact")
        return GateResult(False, MISSING_PHASE3_PRODUCTION_CLOSURE, tuple(missing), exit_code=2)
    result = _run_gate(
        repo_root,
        [
            str(repo_root / PHASE3_CLOSURE_SCRIPT),
            "--closure-profile",
            "production",
            "--p0-e2e-artifact",
            p0_e2e_artifact,
            "--benchmark-artifact",
            benchmark_artifact,
        ],
    )
    if result.returncode != 0:
        return GateResult(
            False,
            MISSING_PHASE3_PRODUCTION_CLOSURE,
            (f"phase3_gate_exit={result.returncode}", result.stdout.strip(), result.stderr.strip()),
            exit_code=2,
        )
    return GateResult(True, "PHASE3_PRODUCTION_CLOSURE_READY")


def _phase4_production_result(
    repo_root: Path,
    *,
    phase4_evidence_artifact: str | None,
    p0_e2e_artifact: str,
    benchmark_artifact: str,
) -> GateResult:
    if not phase4_evidence_artifact:
        return GateResult(
            False,
            MISSING_PHASE4_PRODUCTION_EVIDENCE,
            ("phase4-evidence-artifact",),
            exit_code=2,
        )
    result = _run_gate(
        repo_root,
        [
            str(repo_root / PHASE4_READINESS_SCRIPT),
            "--readiness-profile",
            "production",
            "--phase4-runtime-evidence-artifact",
            phase4_evidence_artifact,
            "--p0-e2e-artifact",
            p0_e2e_artifact,
            "--benchmark-artifact",
            benchmark_artifact,
        ],
    )
    if result.returncode != 0:
        return GateResult(
            False,
            MISSING_PHASE4_PRODUCTION_EVIDENCE,
            (f"phase4_gate_exit={result.returncode}", result.stdout.strip(), result.stderr.strip()),
            exit_code=2,
        )
    return GateResult(True, "PHASE4_PRODUCTION_EVIDENCE_READY")


def _first_failure(results: Iterable[GateResult]) -> GateResult | None:
    return next((result for result in results if not result.valid), None)


def validate_readiness(args: argparse.Namespace) -> GateResult:
    repo_root = Path(args.repo_root).resolve()
    common_failure = _first_failure((_phase2_owner_result(repo_root), _runtime_inbox_cutover_result(repo_root)))
    if common_failure is not None:
        return common_failure

    if args.lane == "technical":
        technical_failure = _first_failure(
            (
                _phase3_mock_closure_result(repo_root),
                _technical_contracts_result(repo_root),
                _document_readiness_result(repo_root, lane="technical"),
            )
        )
        if technical_failure is not None:
            return technical_failure
        return GateResult(True, "PHASE5_TECHNICAL_READY", exit_code=0)

    phase3_result = _phase3_production_result(
        repo_root,
        p0_e2e_artifact=args.phase3_p0_e2e_artifact,
        benchmark_artifact=args.phase3_benchmark_artifact,
    )
    if not phase3_result.valid:
        return phase3_result
    phase4_result = _phase4_production_result(
        repo_root,
        phase4_evidence_artifact=args.phase4_evidence_artifact,
        p0_e2e_artifact=args.phase3_p0_e2e_artifact,
        benchmark_artifact=args.phase3_benchmark_artifact,
    )
    if not phase4_result.valid:
        return phase4_result
    business_contracts_result = _business_contracts_result(repo_root)
    if not business_contracts_result.valid:
        return business_contracts_result
    matrix_result = _document_readiness_result(repo_root, lane="business")
    if not matrix_result.valid:
        return matrix_result
    return GateResult(True, "PHASE5_BUSINESS_READY", exit_code=0)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("technical", "business"), default="technical")
    parser.add_argument("--phase3-p0-e2e-artifact", help="Path to the Phase3 production P0 E2E artifact JSON.")
    parser.add_argument(
        "--phase3-benchmark-artifact",
        help="Path to the Phase3 production-scale benchmark artifact JSON.",
    )
    parser.add_argument("--phase4-evidence-artifact", help="Path to the Phase4 production evidence artifact JSON.")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root to validate. Defaults to the script repository.",
    )
    return parser.parse_args(argv)


def _print_failure(result: GateResult) -> None:
    print(f"Phase 5 readiness failed: {result.reason}")
    details = tuple(detail for detail in result.details if detail)
    if details:
        print("details=" + " | ".join(details))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_readiness(args)
    if not result.valid:
        _print_failure(result)
        return result.exit_code
    print(f"Phase 5 readiness passed: lane={args.lane}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
