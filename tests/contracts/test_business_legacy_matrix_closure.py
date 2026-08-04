"""Business legacy matrix closure guardrail."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_CLEANUP_MATRIX_CSV = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
BUSINESS_LEGACY_ABSENCE_LEDGER_CSV = REPO_ROOT / "docs" / "architecture" / "business-legacy-absence-ledger.csv"


def _matrix_rows() -> list[dict[str, str]]:
    with LEGACY_CLEANUP_MATRIX_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _ledger_rows() -> list[dict[str, str]]:
    with BUSINESS_LEGACY_ABSENCE_LEDGER_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_business_legacy_matrix_has_no_dedicated_drop_items() -> None:
    rows = _matrix_rows()

    assert Counter(row["drop_phase"] for row in rows)["phase5-business"] == 0


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

    allowed_targets_by_semantics = {
        "[phase4] NG 退货/处理业务流程": {
            "material-flow:ng_return_item_service.NgReturnItemService",
        },
        "[phase4] Bin Cell 预约业务流程": {
            "material-flow:bin_cell_reservation_service.WorklineBinCellReservationService",
        },
    }
    mismatched_targets = [
        row["entry_id"]
        for row in ledger_rows
        if (allowed_targets := allowed_targets_by_semantics.get(row["business_semantics"]))
        and row["target_capability"] not in allowed_targets
    ]
    assert mismatched_targets == []
