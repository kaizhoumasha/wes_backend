"""Business legacy matrix closure guardrail."""

from __future__ import annotations

import csv
import importlib
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from scripts import generate_legacy_matrix
from scripts.generate_legacy_matrix import parse_entries

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_CLEANUP_MATRIX_CSV = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
BUSINESS_LEGACY_ABSENCE_LEDGER_CSV = REPO_ROOT / "docs" / "architecture" / "business-legacy-absence-ledger.csv"


def test_git_grep_merges_tracked_and_untracked_matches(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "tracked.py").write_text("class TrackedService:\n    pass\n", encoding="utf-8")
    (source_root / "untracked.py").write_text("class UntrackedService:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(generate_legacy_matrix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        generate_legacy_matrix.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="src/tracked.py:1:class TrackedService:\n",
        ),
    )

    matches = generate_legacy_matrix.git_grep(r"class .*Service", ["src"])

    assert matches == [
        "src/tracked.py:1:class TrackedService:",
        "src/untracked.py:1:class UntrackedService:",
    ]


def _matrix_rows() -> list[dict[str, str]]:
    with LEGACY_CLEANUP_MATRIX_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _ledger_rows() -> list[dict[str, str]]:
    with BUSINESS_LEGACY_ABSENCE_LEDGER_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_workline_start_legacy_outbox_port_is_absent_after_phase10() -> None:
    rows_by_symbol = {
        row["symbol_or_route"]: row
        for row in _matrix_rows()
        if row["relative_path"] == "src/app/workline/services/workline_start_service.py"
    }

    assert "OutboxRepositoryPort" not in rows_by_symbol


def test_business_legacy_matrix_has_no_dedicated_drop_items() -> None:
    rows = _matrix_rows()

    assert Counter(row["drop_phase"] for row in rows)["phase5-business"] == 0


def test_retired_phase5_business_carriers_are_absent_from_generator_and_ledgers() -> None:
    retired_paths = {
        "src/app/runtime/capabilities/material_flow/contracts/rough_sorter.py",
        "src/app/runtime/capabilities/material_flow/contracts/rough_sorter_context.py",
        "src/app/runtime/capabilities/material_flow/contracts/smt_inbound_handoff_reason.py",
        "src/app/runtime/capabilities/material_flow/contracts/smt_usage_policy.py",
        "src/app/runtime/capabilities/material_flow/contracts/sorting_inbound_context.py",
        "src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py",
        "src/app/runtime/orchestration/repositories/smt_inbound_handoff_repository.py",
        "src/app/workline/domain/contexts/rough_sorter.py",
        "src/app/workline/domain/contexts/smt_sorting_inbound.py",
        "src/app/workline/domain/contracts/rough_sorter.py",
        "src/app/workline/domain/services/smt_inbound_handoff_reason.py",
        "src/app/workline/domain/services/smt_inbound_handoff_route_service.py",
        "src/app/workline/domain/services/smt_usage_policy.py",
        "src/app/workline/models/plugin_binding.py",
        "src/app/workline/repositories/plugin_binding_repository.py",
        "src/app/workline/repositories/smt_inbound_handoff_repository.py",
        "src/app/workline/services/ng_return_item_service.py",
        "src/app/workline/services/plugin_binding_service.py",
        "src/app/workline/services/smt_inbound_handoff_service.py",
    }

    migration_paths = {
        *generate_legacy_matrix.MIGRATED_DOMAIN_IMPLS,
        *generate_legacy_matrix.MIGRATED_DOMAIN_IMPLS.values(),
        *generate_legacy_matrix.MIGRATED_REPOSITORIES,
        *generate_legacy_matrix.MIGRATED_REPOSITORIES.values(),
    }
    generated_paths = {entry.relative_path for entry in parse_entries()}
    matrix_paths = {row["relative_path"] for row in _matrix_rows()}
    ledger_paths = {row["relative_path"] for row in _ledger_rows()}

    assert migration_paths.isdisjoint(retired_paths)
    assert generate_legacy_matrix.ACTIVE_PLATFORM_PATHS.isdisjoint(retired_paths)
    assert generated_paths.isdisjoint(retired_paths)
    assert matrix_paths.isdisjoint(retired_paths)
    assert ledger_paths.isdisjoint(retired_paths)


def test_business_carrier_rows_remain_auditable_before_business_cleanup() -> None:
    rows = _matrix_rows()
    ledger_rows = _ledger_rows()
    phase4_carriers = [row for row in rows if row["phase4_carrier"].lower() == "true"]
    invalid_rows = [
        row["entry_id"]
        for row in phase4_carriers
        if row["drop_phase"] != "phase4"
        or not row["target_path"]
        or not row["target_capability"]
        or not row["blocking_tests"]
    ]

    assert phase4_carriers, "expected phase4 carriers in legacy-cleanup matrix"
    assert invalid_rows == []
    matrix_by_entry_id = {row["entry_id"]: row for row in phase4_carriers}
    ledger_by_entry_id = {row["entry_id"]: row for row in ledger_rows}
    assert matrix_by_entry_id.keys() == ledger_by_entry_id.keys()
    assert all(
        matrix_by_entry_id[entry_id]["business_semantics"] == ledger_row["business_semantics"]
        for entry_id, ledger_row in ledger_by_entry_id.items()
    )

    unresolved_targets: list[str] = []
    for row in ledger_rows:
        namespace, _, identifier = row["target_capability"].partition(":")
        if namespace not in {"material-flow", "workline-config"} or "." not in identifier:
            unresolved_targets.append(row["entry_id"])
            continue
        module_name, symbol = identifier.rsplit(".", 1)
        module_prefix = (
            "src.app.runtime.capabilities.material_flow"
            if namespace == "material-flow"
            else "src.app.workline.services"
        )
        module = importlib.import_module(f"{module_prefix}.{module_name}")
        if not hasattr(module, symbol):
            unresolved_targets.append(row["entry_id"])

    assert unresolved_targets == []
