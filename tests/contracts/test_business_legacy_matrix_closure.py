"""Business legacy matrix closure guardrail."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_CLEANUP_MATRIX_CSV = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
LEGACY_CLEANUP_MATRIX_DOC = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.md"
BUSINESS_LEGACY_ABSENCE_LEDGER_CSV = REPO_ROOT / "docs" / "architecture" / "business-legacy-absence-ledger.csv"
BUSINESS_LEGACY_ABSENCE_LEDGER_DOC = REPO_ROOT / "docs" / "architecture" / "business-legacy-absence-ledger.md"
CURRENT_STATE_ARCHITECTURE_DOCS = (
    REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.md",
    REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md",
    REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-execution-plan.md",
    REPO_ROOT / "docs" / "architecture" / "phase3-phase4-production-evidence-bundle.md",
)
STALE_BUSINESS_BLOCKER_TOKENS = (
    "MISSING_RUNTIME_PRODUCTION_CLOSURE",
    "MISSING_RUNTIME_PRODUCTION_EVIDENCE",
    "LEGACY_MATRIX_BUSINESS_ITEMS_OPEN",
    "blocked-until-production-evidence",
    "继续阻塞",
    "当前失败",
)


def _matrix_rows() -> list[dict[str, str]]:
    with LEGACY_CLEANUP_MATRIX_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _ledger_rows() -> list[dict[str, str]]:
    with BUSINESS_LEGACY_ABSENCE_LEDGER_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _summary_count(path: Path, metric_prefix: str) -> int:
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip(" *") for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].startswith(metric_prefix):
            return int(cells[1])
    raise AssertionError(f"{metric_prefix} summary missing in {path}")


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
    assert len(phase4_carriers) == _summary_count(LEGACY_CLEANUP_MATRIX_DOC, "phase4_carrier")
    assert len(phase4_carriers) == _summary_count(BUSINESS_LEGACY_ABSENCE_LEDGER_DOC, "total_entries")
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


def test_business_legacy_inventory_statements_match_current_csvs() -> None:
    matrix_rows = _matrix_rows()
    ledger_rows = _ledger_rows()
    doc_text = LEGACY_CLEANUP_MATRIX_DOC.read_text(encoding="utf-8")
    owner_counts = Counter(row["current_owner"] for row in matrix_rows)
    disposition_counts = Counter(row["cleanup_disposition"] for row in ledger_rows)
    runtime_tests = sum(row["relative_path"].startswith("tests/workline_runtime/") for row in matrix_rows)
    guardrail_seeds = sum(row["notes"] == "guardrail_seed_scope" for row in matrix_rows)
    wms_seeds = sum("WMS_INTEGRATION_BOUNDARY seed" in row["business_semantics"] for row in matrix_rows)
    correlation_seeds = sum("EXECUTION_CORRELATION_BOUNDARY seed" in row["business_semantics"] for row in matrix_rows)
    capability_import_seeds = sum(
        "CAPABILITY_IMPLEMENTATION_IMPORT seed" in row["business_semantics"] for row in matrix_rows
    )
    phase2_rebuild = sum(row["drop_phase"] == "phase2" and row["strategy"] == "rebuild" for row in matrix_rows)

    assert f"### 7.1 workline（{owner_counts['workline']} entries）" in doc_text
    assert f"### 7.2 workline_runtime（{owner_counts['workline_runtime']} entries）" in doc_text
    assert f"{runtime_tests} 条仍在核心测试树中的 runtime 合同" in doc_text
    assert f"### 7.5 guardrail_seed_scope（{guardrail_seeds} entries）" in doc_text
    assert f"WMS_INTEGRATION_BOUNDARY（WMS import，{wms_seeds} 条）" in doc_text
    assert f"EXECUTION_CORRELATION_BOUNDARY（session FK，{correlation_seeds} 条）" in doc_text
    assert f"CAPABILITY_IMPLEMENTATION_IMPORT（capability forbidden import，{capability_import_seeds} 条）" in doc_text
    assert f"phase2 rebuild 总计 {phase2_rebuild} 条" in doc_text
    assert f"phase4 业务流程（{len(ledger_rows)} entries）" in doc_text
    assert f"CSV {len(matrix_rows)} 条" in doc_text
    assert (
        f"{len(ledger_rows)} 条 phase4 carrier："
        f"{disposition_counts['moved']} 行 moved、"
        f"{disposition_counts['kept-config-only']} 行 kept-config-only、"
        f"{disposition_counts['already-removed']} 行 already-removed，0 pending"
    ) in doc_text


def test_business_legacy_matrix_status_is_final_cleanup_complete() -> None:
    text = LEGACY_CLEANUP_MATRIX_DOC.read_text(encoding="utf-8")

    assert "workline_business_scope_status: complete" in text


def test_current_state_docs_do_not_keep_stale_business_blockers() -> None:
    stale_matches = [
        f"{path.relative_to(REPO_ROOT)}:{token}"
        for path in CURRENT_STATE_ARCHITECTURE_DOCS
        for token in STALE_BUSINESS_BLOCKER_TOKENS
        if token in path.read_text(encoding="utf-8")
    ]

    assert stale_matches == []
