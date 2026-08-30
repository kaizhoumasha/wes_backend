"""Release operational readiness read model 的精确跨域只读边界。"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.generate_legacy_matrix import parse_entries

REPO_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_PATH = "src/app/runtime/orchestration/repositories/release_operational_readiness_repository.py"
ARCHITECTURE_TEST = "tests/architecture/test_release_operational_readiness_repository_boundary.py"
POSTGRESQL_TEST = "tests/integration/test_release_operational_readiness_postgresql.py"
RETAIN_ENTRY_ID = f"legacy:{REPOSITORY_PATH}:ReleaseOperationalReadinessRepository"
REJECTED_SEED_ENTRY_ID = f"legacy:{REPOSITORY_PATH}:<file>#CAPABILITY_IMPLEMENTATION_IMPORT"

_APPROVED_FOUR_LEDGER_IMPORTS = {
    "device_command": {
        "src.app.device.models.command": {("CommandStatus", ""), ("DeviceCommand", "")},
    },
    "transport_task": {
        "src.app.transport.contracts": {("TransportTaskStatus", "")},
        "src.app.transport.models": {("TransportTask", "")},
    },
    "inbound_evidence": {
        "src.app.execution.models.inbound_evidence": {
            ("InboundEvidence", ""),
            ("InboundEvidenceApplyStatus", ""),
        },
    },
    "wms_confirmation": {
        "src.app.execution.models.wms_confirmation": {
            ("WmsConfirmation", ""),
            ("WmsConfirmationStatus", ""),
        },
    },
}
_EXPECTED_CROSS_DOMAIN_IMPORTS = {
    module: names
    for ledger_imports in _APPROVED_FOUR_LEDGER_IMPORTS.values()
    for module, names in ledger_imports.items()
}
_FORBIDDEN_WRITE_CALLS = frozenset({"add", "add_all", "commit", "delete", "flush", "insert", "merge", "update"})
_EXPECTED_ALLOWLIST_ROW = (
    "CAPABILITY_IMPLEMENTATION_IMPORT|"
    f"{REPOSITORY_PATH}|"
    "批准的四账本只读 release read model，只读 ORM metadata，无 dispatch/write|"
    "2026-09-30|"
    f"{RETAIN_ENTRY_ID}|phase10"
)


def _cross_domain_imports(module: ast.Module) -> dict[str, set[tuple[str, str]]]:
    imports: dict[str, set[tuple[str, str]]] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src.app."):
            assert node.level == 0
            imports.setdefault(node.module, set()).update((alias.name, alias.asname or "") for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src.app."):
                    imports.setdefault(alias.name, set()).add((alias.name, alias.asname or ""))
    return imports


def test_release_readiness_repository_imports_only_approved_four_read_only_ledgers() -> None:
    source_path = REPO_ROOT / REPOSITORY_PATH
    module = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path.as_posix())

    assert len(_APPROVED_FOUR_LEDGER_IMPORTS) == 4
    assert _cross_domain_imports(module) == _EXPECTED_CROSS_DOMAIN_IMPORTS
    assert not {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name)
    }.intersection(_FORBIDDEN_WRITE_CALLS)


def test_release_readiness_repository_uses_explicit_retain_matrix_identity() -> None:
    entries = {entry.entry_id: entry for entry in parse_entries()}

    assert REJECTED_SEED_ENTRY_ID not in entries
    entry = entries[RETAIN_ENTRY_ID]
    assert entry.entry_type == "repository"
    assert entry.current_owner == "runtime"
    assert entry.strategy == "retain"
    assert entry.target_path == REPOSITORY_PATH
    assert entry.target_capability == "ReleaseOperationalReadinessRepository"
    assert entry.blocking_tests == f"{ARCHITECTURE_TEST};{POSTGRESQL_TEST}"
    assert entry.drop_phase == "phase10"
    assert entry.notes == "phase10-prelock:runtime:retain"


def test_release_readiness_repository_allowlist_relation_is_exact() -> None:
    allowlist_rows = [
        row
        for row in (REPO_ROOT / "scripts/architecture-guardrails.allowlist").read_text(encoding="utf-8").splitlines()
        if row.startswith(f"CAPABILITY_IMPLEMENTATION_IMPORT|{REPOSITORY_PATH}|")
    ]
    assert allowlist_rows == [_EXPECTED_ALLOWLIST_ROW]

    entry = next(entry for entry in parse_entries() if entry.entry_id == RETAIN_ENTRY_ID)
    rule, path, reason, expires_at, legacy_entry_id, drop_phase = allowlist_rows[0].split("|")
    assert rule == "CAPABILITY_IMPLEMENTATION_IMPORT"
    assert path == entry.relative_path
    assert reason == "批准的四账本只读 release read model，只读 ORM metadata，无 dispatch/write"
    assert expires_at == "2026-09-30"
    assert legacy_entry_id == entry.entry_id
    assert drop_phase == entry.drop_phase
