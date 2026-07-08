"""Guard active code against process-stage naming."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = (
    Path("src"),
    Path("scripts"),
    Path("tests/architecture"),
    Path("tests/contracts"),
    Path("tests/workline_runtime"),
    Path("tests/api"),
    Path("tests/runtime"),
    Path("tests/unit"),
    Path("tests/core"),
    Path("tests/database"),
    Path("tests/resource"),
    Path("tests/workline"),
    Path("tests/wms_integration"),
    Path("tests/load"),
)

IGNORED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "reports",
    }
)

IGNORED_PREFIXES = (
    Path("src/static"),
    Path("migrations/versions"),
    Path("docs/archive"),
    Path("docs/superpowers/archive"),
)

INTENTIONAL_PROCESS_NAMING_ALLOWLIST: dict[Path, str] = {
    Path(
        "scripts/architecture-guardrails.allowlist"
    ): "historical guardrail allowlist data preserves legacy drop_phase values",
    Path("scripts/generate_legacy_matrix.py"): "legacy cleanup matrix generator owns historical audit schema fields",
    Path(
        "tests/architecture/test_cleanup_matrix_guardrail.py"
    ): "legacy cleanup matrix schema guardrail owns historical audit fields",
    Path("tests/architecture/test_phase0_legacy_matrix_contract.py"): "historical matrix baseline contract",
    Path("tests/architecture/test_phase2_runtime_status_owner_guardrail.py"): (
        "historical runtime-status ownership guardrail kept under original milestone name"
    ),
    Path("tests/architecture/test_process_naming_guardrail.py"): "guardrail defines the forbidden tokens it enforces",
    Path(
        "tests/contracts/test_business_legacy_matrix_closure.py"
    ): "business legacy matrix closure checks historical audit columns",
    Path(
        "tests/contracts/test_phase4_design_docs.py"
    ): "historical design package contract for the original material-flow milestone",
    Path(
        "tests/migrations/test_phase1_device_fk_ring_dissolve.py"
    ): "migration semantic contract for immutable historical migration",
    Path("tests/migrations/test_phase4_runtime_location_reservation_migration.py"): (
        "migration semantic contract for immutable historical migration"
    ),
}

PROCESS_NAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "path/import phase token",
        re.compile(r"phase[0-9](?=[^0-9]|$)|phase_[0-9]|phase[_ -][a-z](?=[^a-z0-9]|$)", re.IGNORECASE),
    ),
    ("symbol phase token", re.compile(r"\bPhase[0-9]\b|\bPhase[_ -][A-Z]\b|\bPHASE[0-9]_|\bPHASE[_ -][A-Z]\b")),
    ("runtime phase prefix", re.compile(r"\bphase[0-9]:", re.IGNORECASE)),
    ("script phase token", re.compile(r"(?:check|compose|run)_phase[0-9](?=[^0-9]|$)", re.IGNORECASE)),
    ("architecture phase option", re.compile(r"architecture-guardrails\.sh\s+--phase|\bARCHITECTURE_PHASE\b")),
    ("refactor process phrase", re.compile(r"burn-down|technical lane|business lane|final cleanup", re.IGNORECASE)),
)


def _is_ignored(relative_path: Path) -> bool:
    if relative_path in INTENTIONAL_PROCESS_NAMING_ALLOWLIST:
        return True
    if any(part in IGNORED_PARTS for part in relative_path.parts):
        return True
    return any(relative_path == prefix or relative_path.is_relative_to(prefix) for prefix in IGNORED_PREFIXES)


def _iter_scan_files() -> list[Path]:
    paths: list[Path] = []
    for root in SCAN_ROOTS:
        absolute_root = REPO_ROOT / root
        if absolute_root.is_file():
            candidates = [absolute_root]
        elif absolute_root.exists():
            candidates = [path for path in absolute_root.rglob("*") if path.is_file()]
        else:
            candidates = []
        for path in candidates:
            relative_path = path.relative_to(REPO_ROOT)
            if not _is_ignored(relative_path):
                paths.append(relative_path)
    return sorted(paths)


def _matches(relative_path: Path) -> list[str]:
    path_text = relative_path.as_posix()
    try:
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8", errors="ignore")

    offenders: list[str] = []
    for label, pattern in PROCESS_NAME_PATTERNS:
        if pattern.search(path_text):
            offenders.append(f"{relative_path}: {label} in path")
        for line_number, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{relative_path}:{line_number}: {label}")
    return offenders


def test_active_code_does_not_use_process_phase_names() -> None:
    offenders: list[str] = []
    for relative_path in _iter_scan_files():
        offenders.extend(_matches(relative_path))

    assert not offenders, "Active code/test/script paths contain process-stage names:\n" + "\n".join(offenders[:200])


def test_process_naming_guardrail_rejects_stale_script_and_option_tokens() -> None:
    examples = (
        "scripts/check_phase3_closure_gate.py",
        "scripts/compose_phase3_runtime_artifact.py",
        "scripts/run_phase3_runtime_benchmarks.py",
        "tests/resource/test_resource_phase_b_contract.py",
        "Phase B resource contract",
        "architecture-guardrails.sh --phase 4",
        "ARCHITECTURE_PHASE=phase4",
    )

    for example in examples:
        assert any(pattern.search(example) for _, pattern in PROCESS_NAME_PATTERNS), example
