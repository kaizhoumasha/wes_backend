"""Guard active code against process-stage naming."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = (
    Path("Jenkinsfile"),
    Path("Jenkinsfile.backend-ci"),
    Path("src"),
    Path("scripts"),
    Path("tests"),
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
        re.compile(
            r"(?<![a-z0-9])phase[ _-]?[0-9]+(?=[^0-9]|$)|(?<![a-z0-9])phase[_ -][a-z](?=[^a-z0-9]|$)",
            re.IGNORECASE,
        ),
    ),
    ("symbol phase token", re.compile(r"\bPhase[0-9]\b|\bPhase[_ -][A-Z]\b|\bPHASE[0-9]_|\bPHASE[_ -][A-Z]\b")),
    ("runtime phase prefix", re.compile(r"\bphase[0-9]:", re.IGNORECASE)),
    ("English numbered stage token", re.compile(r"(?<![a-z0-9])stage[ _-]?[0-9]+(?=[^0-9]|$)")),
    ("Chinese numbered stage token", re.compile(r"阶段[ _-]?[0-9]+")),
    ("runtime migration phrase", re.compile(r"runtime[ _-]migration", re.IGNORECASE)),
    ("runtime migration packet token", re.compile(r"runtime[ _-]migration[ _-]C[0-9][a-z]?", re.IGNORECASE)),
    ("script phase token", re.compile(r"(?:check|compose|run)_phase[0-9](?=[^0-9]|$)", re.IGNORECASE)),
    ("wave token", re.compile(r"(?<![a-z0-9])wave[ _-]?[0-9]+(?=[^0-9]|$)", re.IGNORECASE)),
    ("packet milestone token", re.compile(r"(?<![a-z0-9])packet[ _-][a-z](?=[^a-z0-9]|$)", re.IGNORECASE)),
    ("architecture phase option", re.compile(r"architecture-guardrails\.sh\s+--phase|\bARCHITECTURE_PHASE\b")),
    (
        "refactor process phrase",
        re.compile(r"burn[ _-]down|technical[ _-]lane|business[ _-]lane|final[ _-]cleanup", re.IGNORECASE),
    ),
    (
        "guardrail rule id shorthand",
        re.compile(r"(?<![A-Za-z0-9])(?:C[1-5][a-z]?|R-?I3[a-c]?|R-WLR)(?![A-Za-z0-9])", re.IGNORECASE),
    ),
    ("guardrail function shorthand", re.compile(r"\brule_(?:c[1-5]|ri3[a-c]?|wlr)(?:_|$)", re.IGNORECASE)),
    ("legacy runtime shorthand alias", re.compile(r"(?<![A-Za-z0-9])wlr(?![A-Za-z0-9])", re.IGNORECASE)),
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


def test_process_naming_guardrail_scans_default_test_tree_and_ci_file() -> None:
    assert Path("tests") in SCAN_ROOTS
    assert Path("Jenkinsfile") in SCAN_ROOTS
    assert Path("Jenkinsfile.backend-ci") in SCAN_ROOTS


def test_process_naming_guardrail_rejects_stale_script_and_option_tokens() -> None:
    examples = (
        "scripts/check_phase3_closure_gate.py",
        "scripts/compose_phase3_runtime_artifact.py",
        "scripts/run_phase3_runtime_benchmarks.py",
        "tests/resource/test_resource_phase_b_contract.py",
        "tests/mock/material_flow/test_wave2_wave3_mock_acceptance.py",
        "Wave2 runtime capability builder",
        "Wave 2 runtime capability builder",
        "wave-2 runtime capability builder",
        "Phase 1 runtime entity",
        "阶段 3 runtime migration artifact",
        "runtime migration cleanup",
        "phase 3 benchmark artifact",
        "after_stage6_cleanup",
        "runtime migration C5a mirror",
        "Packet C runtime entity",
        "packet C runtime entity",
        "packet-c runtime entity",
        "packet_c runtime entity",
        "TECHNICAL_LANE_FORBIDDEN_MODULES",
        "business-lane cleanup",
        "final_cleanup gate",
        "burn_down ledger",
        "Phase B resource contract",
        "architecture-guardrails.sh --phase 4",
        "ARCHITECTURE_PHASE=phase4",
        "tests/architecture/test_c3_authority_metadata_guardrail.py",
        "tests/architecture/test_c4_device_command_fields_guardrail.py",
        "tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py",
        "tests/architecture/test_wlr_import_guardrail.py",
        "rule_c3",
        "rule_c4",
        "rule_ri3b",
        "rule_ri3c",
        "rule_wlr_import",
        "[C3] warning",
        "[C4] violation",
        "R-I3b seed allowlist",
        "R-I3c inbound normalizer",
        "R-WLR production import",
        "wlr allowlist strict mode",
    )

    for example in examples:
        assert any(pattern.search(example) for _, pattern in PROCESS_NAME_PATTERNS), example
