"""Business legacy absence ledger contract."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.check_business_legacy_absence_gate import (
    LEDGER_HEADER,
    STRICT_DISPOSITIONS,
    _row_final_gate_failures,
    validate_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "docs" / "architecture" / "business-legacy-absence-ledger.csv"
MATRIX_PATH = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
MATERIAL_FLOW_CARRIER_FIELD = "phase" + "4_carrier"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_business_legacy_absence_ledger_passes_draft_gate() -> None:
    result = validate_ledger(REPO_ROOT, mode="draft")

    assert result.valid, result.details


def test_business_legacy_absence_ledger_header_and_entry_set_match_matrix() -> None:
    with LEDGER_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or ()) == LEDGER_HEADER
        ledger_rows = list(reader)

    matrix_rows = [row for row in _rows(MATRIX_PATH) if row[MATERIAL_FLOW_CARRIER_FIELD] == "True"]

    assert [row["entry_id"] for row in ledger_rows] == sorted(row["entry_id"] for row in ledger_rows)
    assert {row["entry_id"] for row in ledger_rows} == {row["entry_id"] for row in matrix_rows}


def test_business_legacy_absence_ledger_tracks_current_surface_states() -> None:
    rows = _rows(LEDGER_PATH)
    tracked_states = {row["tracked_state"] for row in rows}
    dispositions = {row["cleanup_disposition"] for row in rows}

    assert {"test-only", "already-removed"}.issubset(tracked_states)
    assert "active-source" not in tracked_states
    assert {"moved", "kept-config-only", "already-removed"}.issubset(dispositions)
    assert "test-only-migrated" not in dispositions
    assert "pending" not in dispositions
    assert all(row["target_capability_status"] == "mapped" for row in rows)


def test_final_gate_rejects_pending_current_pr_provenance() -> None:
    row = {
        "entry_id": "legacy:example",
        "cleanup_disposition": "deleted",
        "semantic_status": "semantics-covered",
        "target_capability_status": "mapped",
        "reference_scan_status": "clean",
        "delete_commit": "pending-current-pr",
        "relative_path": "src/example.py",
    }

    failures = _row_final_gate_failures(row, "final", REPO_ROOT)

    assert any("pending-current-pr cannot enter final gate" in failure for failure in failures)


def test_final_ledger_has_real_delete_commits_and_existing_evidence_paths() -> None:
    rows = _rows(LEDGER_PATH)

    assert validate_ledger(REPO_ROOT, mode="final").valid
    assert all(row["delete_commit"] != "pending-current-pr" for row in rows)
    assert all(row["delete_commit"] for row in rows if row["cleanup_disposition"] in STRICT_DISPOSITIONS)
    for row in rows:
        for field in ("golden_fixture", "contract_tests"):
            assert all((REPO_ROOT / path).exists() for path in row[field].split(";") if path)
