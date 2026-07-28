"""Validate business legacy absence ledger and strict absence state."""

from __future__ import annotations

import argparse
import ast
import csv
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


MATERIAL_FLOW_CARRIER_FIELD = "phase" + "4_carrier"

LEDGER_HEADER = (
    "entry_id",
    "entry_type",
    "relative_path",
    "symbol_or_route",
    "current_owner",
    "business_semantics",
    MATERIAL_FLOW_CARRIER_FIELD,
    "tracked_state",
    "semantic_status",
    "cleanup_disposition",
    "target_capability",
    "target_capability_status",
    "golden_fixture",
    "contract_tests",
    "reference_scan_status",
    "external_alias_status",
    "delete_commit",
    "notes",
)

VALID_ENUMS = {
    "tracked_state": frozenset({"active-source", "test-only", "already-removed", "schema-deferred"}),
    "semantic_status": frozenset({"semantics-covered", "semantics-obsolete", "semantics-unverified"}),
    "cleanup_disposition": frozenset(
        {"pending", "moved", "deleted", "kept-config-only", "already-removed", "test-only-migrated", "schema-deferred"}
    ),
    "target_capability_status": frozenset({"mapped", "obsolete", "not-applicable", "blocked"}),
    "reference_scan_status": frozenset({"pending", "clean", "allowed-reference-only", "blocked"}),
    "external_alias_status": frozenset(
        {"not-applicable", "internal-only", "external-contract-blocker", "breaking-change-deferred"}
    ),
}

STRICT_DISPOSITIONS = frozenset({"moved", "deleted", "test-only-migrated"})
TARGET_CAPABILITY_NAMESPACES = frozenset({"runtime", "material-flow", "external", "workline-config"})
FINAL_BLOCKED_REFERENCE_SCAN_STATUSES = frozenset({"pending", "blocked"})
FINAL_BLOCKED_TARGET_CAPABILITY_STATUSES = frozenset({"blocked"})
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
LEDGER_MATRIX_FIELDS = (
    "entry_id",
    "entry_type",
    "relative_path",
    "symbol_or_route",
    "current_owner",
    "business_semantics",
    MATERIAL_FLOW_CARRIER_FIELD,
)
LEDGER_REQUIRED_FIELDS = (
    "target_capability_status",
    "golden_fixture",
    "contract_tests",
    "reference_scan_status",
    "external_alias_status",
)

MATRIX_PATH = Path("docs/architecture/legacy-cleanup-matrix.csv")
LEDGER_PATH = Path("docs/architecture/business-legacy-absence-ledger.csv")
MATERIAL_FLOW_CONTRACTS_ROOT = Path("src/app/runtime/capabilities/material_flow/contracts")
MATERIAL_FLOW_CONTRACTS_PACKAGE = "src.app.runtime.capabilities.material_flow.contracts"

SCAN_ROOTS = (
    Path("src"),
    Path("scripts"),
    Path("tests"),
    Path("migrations/versions"),
    Path("docs/architecture"),
    Path("docs/contracts"),
    Path("docs/superpowers/plans"),
    Path("pyproject.toml"),
    Path("alembic.ini"),
)
SCAN_EXCLUDED_PARTS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", "reports"})
LEDGER_AUDIT_DOCS = frozenset(
    {
        MATRIX_PATH,
        Path("docs/architecture/legacy-cleanup-matrix.md"),
        LEDGER_PATH,
        Path("docs/architecture/business-legacy-absence-ledger.md"),
        Path("docs/superpowers/archive/plans/2026-07-07-" + "phase" + "5-business-legacy-destructive-cleanup.md"),
        Path("scripts/generate_legacy_matrix.py"),
        Path("tests/architecture/test_workline_domain_boundary.py"),
        Path("tests/architecture/test_business_legacy_absence_guardrail.py"),
    }
)

FORBIDDEN_MATERIAL_FLOW_CONTRACT_IMPORT_PREFIXES = (
    "sqlalchemy",
    "src.app.runtime.capabilities.material_flow.",
    "src.app.runtime.orchestration.repositories",
    "src.app.runtime.orchestration.services",
    "src.app.workline.repositories",
    "src.app.workline.services",
    "src.database",
)


@dataclass(frozen=True)
class GateResult:
    valid: bool
    reason: str
    details: tuple[str, ...] = ()


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return tuple(reader.fieldnames or ()), list(reader)


def _material_flow_matrix_rows(repo_root: Path) -> list[dict[str, str]]:
    _, rows = _read_csv(repo_root / MATRIX_PATH)
    return [row for row in rows if row[MATERIAL_FLOW_CARRIER_FIELD] == "True"]


def _ledger_rows(repo_root: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    return _read_csv(repo_root / LEDGER_PATH)


def _git_tracked_files(repo_root: Path) -> set[str]:
    git_executable = which("git")
    if git_executable is None:
        raise RuntimeError("git executable not found")

    result = subprocess.run(  # noqa: S603 - fixed git executable and fixed ls-files subcommand.
        [git_executable, "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.splitlines())


def _expected_tracked_state(row: dict[str, str], tracked_files: set[str], repo_root: Path) -> str:
    relative_path = row["relative_path"]
    if relative_path not in tracked_files or not (repo_root / relative_path).exists():
        return "already-removed"
    if relative_path.startswith("tests/"):
        return "test-only"
    return "active-source"


def _module_name(relative_path: str) -> str | None:
    if not relative_path.endswith(".py"):
        return None
    return relative_path.removesuffix(".py").replace("/", ".")


def _iter_scan_files(repo_root: Path) -> Iterable[Path]:
    for root in SCAN_ROOTS:
        absolute = repo_root / root
        if not absolute.exists():
            continue
        if absolute.is_file():
            yield absolute
            continue
        for path in sorted(absolute.rglob("*")):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(repo_root).parts
            if any(part in SCAN_EXCLUDED_PARTS for part in relative_parts):
                continue
            if path.suffix not in {".py", ".md", ".csv", ".yaml", ".yml", ".toml", ".ini", ".sh"}:
                continue
            yield path


def _strict_reference_tokens(row: dict[str, str]) -> tuple[str, ...]:
    module_name = _module_name(row["relative_path"])
    tokens = [row["relative_path"]]
    if module_name is not None:
        tokens.append(module_name)
    return tuple(tokens)


def strict_reference_violations(repo_root: Path, rows: Sequence[dict[str, str]]) -> tuple[str, ...]:
    strict_rows = [row for row in rows if row["cleanup_disposition"] in STRICT_DISPOSITIONS]
    if not strict_rows:
        return ()

    token_to_entry = {token: row["entry_id"] for row in strict_rows for token in _strict_reference_tokens(row) if token}
    violations: list[str] = []
    for path in _iter_scan_files(repo_root):
        relative = path.relative_to(repo_root)
        if relative in LEDGER_AUDIT_DOCS:
            continue
        text = path.read_text(encoding="utf-8")
        for token, entry_id in token_to_entry.items():
            if token in text:
                violations.append(f"{relative}:{entry_id}:{token}")
    return tuple(sorted(set(violations)))


def _module_path_from_contract_file(repo_root: Path, path: Path) -> str:
    module_parts = path.relative_to(repo_root).with_suffix("").parts
    if module_parts[-1] == "__init__":
        module_parts = module_parts[:-1]
    return ".".join(module_parts)


def _resolve_import_from_module(repo_root: Path, path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    current_module = _module_path_from_contract_file(repo_root, path)
    current_package = current_module if path.name == "__init__.py" else current_module.rpartition(".")[0]
    package_parts = current_package.split(".") if current_package else []
    keep_count = len(package_parts) - (node.level - 1)
    if keep_count < 0:
        return None

    resolved_parts = package_parts[:keep_count]
    if node.module:
        resolved_parts.extend(node.module.split("."))
    return ".".join(resolved_parts)


def _material_flow_contract_imports(repo_root: Path, path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(repo_root, path, node)
            if module:
                imports.append(module)
    return tuple(imports)


def material_flow_contract_layer_violations(repo_root: Path) -> tuple[str, ...]:
    contracts_root = repo_root / MATERIAL_FLOW_CONTRACTS_ROOT
    if not contracts_root.exists():
        return ()

    violations: list[str] = []
    for path in sorted(contracts_root.rglob("*.py")):
        for module in _material_flow_contract_imports(repo_root, path):
            if module == MATERIAL_FLOW_CONTRACTS_PACKAGE or module.startswith(f"{MATERIAL_FLOW_CONTRACTS_PACKAGE}."):
                continue
            if any(
                module == prefix.removesuffix(".") or module.startswith(prefix)
                for prefix in FORBIDDEN_MATERIAL_FLOW_CONTRACT_IMPORT_PREFIXES
            ):
                violations.append(f"{path.relative_to(repo_root)}:{module}")
    return tuple(violations)


def _ledger_identity_failures(
    header: tuple[str, ...],
    matrix_by_id: dict[str, dict[str, str]],
    ledger_ids: list[str],
) -> list[str]:
    failures: list[str] = []
    if header != LEDGER_HEADER:
        failures.append(f"{LEDGER_PATH}:header mismatch")
    if len(ledger_ids) != len(set(ledger_ids)):
        failures.append(f"{LEDGER_PATH}:duplicate entry_id")
    if sorted(ledger_ids) != ledger_ids:
        failures.append(f"{LEDGER_PATH}:entry_id not ASCII sorted")
    if set(ledger_ids) != set(matrix_by_id):
        missing = sorted(set(matrix_by_id) - set(ledger_ids))
        extra = sorted(set(ledger_ids) - set(matrix_by_id))
        failures.append(f"{LEDGER_PATH}:entry_id set mismatch missing={missing[:5]} extra={extra[:5]}")
    return failures


def _row_matrix_failures(row: dict[str, str], matrix_row: dict[str, str]) -> list[str]:
    entry_id = row["entry_id"]
    return [
        f"{entry_id}:{field} differs from matrix" for field in LEDGER_MATRIX_FIELDS if row[field] != matrix_row[field]
    ]


def _row_enum_failures(row: dict[str, str]) -> list[str]:
    entry_id = row["entry_id"]
    return [
        f"{entry_id}:{field} invalid value {row[field]!r}"
        for field, values in VALID_ENUMS.items()
        if row[field] not in values
    ]


def _row_state_failures(row: dict[str, str], tracked_files: set[str], repo_root: Path) -> list[str]:
    entry_id = row["entry_id"]
    failures: list[str] = []
    expected_state = _expected_tracked_state(row, tracked_files, repo_root)
    if row["tracked_state"] != expected_state:
        failures.append(f"{entry_id}:tracked_state expected {expected_state}, got {row['tracked_state']}")
    if row[MATERIAL_FLOW_CARRIER_FIELD] != "True":
        failures.append(f"{entry_id}:{MATERIAL_FLOW_CARRIER_FIELD} must be True")
    if row["tracked_state"] == "schema-deferred" and row["cleanup_disposition"] != "schema-deferred":
        failures.append(f"{entry_id}:schema-deferred tracked_state must use schema-deferred disposition")
    if row["tracked_state"] == "already-removed" and row["semantic_status"] == "semantics-unverified":
        failures.append(f"{entry_id}:already-removed row needs covered or obsolete semantics")
    return failures


def _row_target_failures(row: dict[str, str]) -> list[str]:
    entry_id = row["entry_id"]
    target_status = row["target_capability_status"]
    target_capability = row["target_capability"]
    if target_status == "mapped":
        namespace, _, identifier = target_capability.partition(":")
        if namespace not in TARGET_CAPABILITY_NAMESPACES or not identifier:
            return [f"{entry_id}:target_capability must use a known namespace"]
    elif target_status in {"obsolete", "not-applicable"} and target_capability:
        return [f"{entry_id}:{target_status} row must not carry target_capability"]
    return []


def _row_required_field_failures(row: dict[str, str]) -> list[str]:
    entry_id = row["entry_id"]
    return [f"{entry_id}:{field} is required" for field in LEDGER_REQUIRED_FIELDS if not row[field]]


def _git_commit_exists(repo_root: Path, commit: str) -> bool:
    git_executable = which("git")
    if git_executable is None:
        return False
    result = subprocess.run(  # noqa: S603 - fixed git executable and fixed rev-parse subcommand.
        [git_executable, "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _git_commit_deleted_path(repo_root: Path, commit: str, relative_path: str) -> bool:
    git_executable = which("git")
    if git_executable is None:
        return False
    result = subprocess.run(  # noqa: S603 - fixed git executable and fixed diff-tree subcommand.
        [git_executable, "diff-tree", "--no-commit-id", "--name-status", "-r", commit, "--", relative_path],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and any(line.startswith(("D\t", "R")) for line in result.stdout.splitlines())


def _row_evidence_path_failures(row: dict[str, str], repo_root: Path) -> list[str]:
    entry_id = row["entry_id"]
    failures: list[str] = []
    for field in ("golden_fixture", "contract_tests"):
        failures.extend(
            f"{entry_id}:{field} path does not exist: {token}"
            for token in (part.strip() for part in row[field].split(";"))
            if token and not (repo_root / token).exists()
        )
    return failures


def _row_final_gate_failures(row: dict[str, str], mode: str, repo_root: Path) -> list[str]:
    if mode != "final":
        return []

    entry_id = row["entry_id"]
    failures: list[str] = []
    if row["cleanup_disposition"] == "pending":
        failures.append(f"{entry_id}:pending cleanup_disposition cannot enter final gate")
    if row["semantic_status"] == "semantics-unverified":
        failures.append(f"{entry_id}:semantics-unverified cannot enter final gate")
    if row["target_capability_status"] in FINAL_BLOCKED_TARGET_CAPABILITY_STATUSES:
        failures.append(f"{entry_id}:blocked target capability cannot enter final gate")
    if row["reference_scan_status"] in FINAL_BLOCKED_REFERENCE_SCAN_STATUSES:
        failures.append(f"{entry_id}:{row['reference_scan_status']} reference scan cannot enter final gate")
    if row["cleanup_disposition"] in STRICT_DISPOSITIONS and not row["delete_commit"]:
        failures.append(f"{entry_id}:strict disposition requires delete_commit in final gate")
    delete_commit = row["delete_commit"]
    if delete_commit == "pending-current-pr":
        failures.append(f"{entry_id}:pending-current-pr cannot enter final gate")
    elif row["cleanup_disposition"] in STRICT_DISPOSITIONS:
        if not GIT_COMMIT_PATTERN.fullmatch(delete_commit) or not _git_commit_exists(repo_root, delete_commit):
            failures.append(f"{entry_id}:delete_commit must resolve to a real commit")
        elif not _git_commit_deleted_path(repo_root, delete_commit, row["relative_path"]):
            failures.append(f"{entry_id}:delete_commit does not delete or migrate {row['relative_path']}")
    return failures


def _row_alias_failures(row: dict[str, str]) -> list[str]:
    if row["cleanup_disposition"] == "deleted" and row["external_alias_status"] == "external-contract-blocker":
        return [f"{row['entry_id']}:external-contract-blocker row cannot be deleted"]
    return []


def _ledger_row_failures(
    row: dict[str, str],
    matrix_row: dict[str, str],
    tracked_files: set[str],
    repo_root: Path,
    *,
    mode: str,
) -> list[str]:
    failures: list[str] = []
    failures.extend(_row_matrix_failures(row, matrix_row))
    failures.extend(_row_enum_failures(row))
    failures.extend(_row_state_failures(row, tracked_files, repo_root))
    failures.extend(_row_target_failures(row))
    failures.extend(_row_required_field_failures(row))
    if mode == "final":
        failures.extend(_row_evidence_path_failures(row, repo_root))
    failures.extend(_row_final_gate_failures(row, mode, repo_root))
    failures.extend(_row_alias_failures(row))
    return failures


def validate_ledger(repo_root: Path, *, mode: str) -> GateResult:
    failures: list[str] = []

    if not (repo_root / LEDGER_PATH).exists():
        return GateResult(False, "BUSINESS_LEGACY_ABSENCE_LEDGER_MISSING", (str(LEDGER_PATH),))

    matrix_rows = _material_flow_matrix_rows(repo_root)
    header, ledger_rows = _ledger_rows(repo_root)
    tracked_files = _git_tracked_files(repo_root)

    matrix_by_id = {row["entry_id"]: row for row in matrix_rows}
    ledger_ids = [row["entry_id"] for row in ledger_rows]
    failures.extend(_ledger_identity_failures(header, matrix_by_id, ledger_ids))

    for row in ledger_rows:
        matrix_row = matrix_by_id.get(row["entry_id"])
        if matrix_row is None:
            continue
        failures.extend(_ledger_row_failures(row, matrix_row, tracked_files, repo_root, mode=mode))

    failures.extend(strict_reference_violations(repo_root, ledger_rows))
    failures.extend(material_flow_contract_layer_violations(repo_root))

    if failures:
        return GateResult(False, "BUSINESS_LEGACY_ABSENCE_OPEN", tuple(failures))
    return GateResult(True, f"BUSINESS_LEGACY_ABSENCE_{mode.upper()}_READY")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("draft", "final"), default="draft")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root to validate. Defaults to the script repository.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_ledger(Path(args.repo_root).resolve(), mode=args.mode)
    if not result.valid:
        print(f"Business legacy absence gate failed: {result.reason}")
        for detail in result.details[:80]:
            print(f"- {detail}")
        if len(result.details) > 80:
            print(f"- ... {len(result.details) - 80} more")
        return 1
    print(f"Business legacy absence gate passed: mode={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
