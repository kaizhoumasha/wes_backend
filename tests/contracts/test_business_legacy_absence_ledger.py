"""Business legacy absence ledger contract."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.check_business_legacy_absence_gate import LEDGER_HEADER, validate_ledger

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
    assert {"moved", "test-only-migrated", "kept-config-only", "already-removed"}.issubset(dispositions)
    assert "pending" not in dispositions
    assert all(row["target_capability_status"] == "mapped" for row in rows)
