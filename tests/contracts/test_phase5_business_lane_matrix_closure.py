"""Phase 5 business lane matrix closure guardrail."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_CLEANUP_MATRIX_CSV = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
LEGACY_CLEANUP_MATRIX_DOC = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.md"
CURRENT_STATE_ARCHITECTURE_DOCS = (
    REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.md",
    REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md",
    REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-execution-plan.md",
    REPO_ROOT / "docs" / "architecture" / "phase3-phase4-production-evidence-bundle.md",
)
STALE_BUSINESS_BLOCKER_TOKENS = (
    "MISSING_PHASE3_PRODUCTION_CLOSURE",
    "MISSING_PHASE4_PRODUCTION_EVIDENCE",
    "LEGACY_MATRIX_BUSINESS_ITEMS_OPEN",
    "blocked-until-production-evidence",
    "继续阻塞",
    "当前失败",
)


def _matrix_rows() -> list[dict[str, str]]:
    with LEGACY_CLEANUP_MATRIX_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_phase5_business_lane_has_no_dedicated_drop_items() -> None:
    rows = _matrix_rows()

    assert Counter(row["drop_phase"] for row in rows)["phase5-business"] == 0


def test_phase4_carrier_rows_remain_auditable_before_business_cleanup() -> None:
    rows = _matrix_rows()
    phase4_carriers = [row for row in rows if row["phase4_carrier"].lower() == "true"]
    invalid_rows = [
        row["entry_id"]
        for row in phase4_carriers
        if row["drop_phase"] != "phase4"
        or not row["target_path"]
        or not row["target_capability"]
        or not row["blocking_tests"]
    ]

    assert len(phase4_carriers) == 104
    assert invalid_rows == []


def test_phase5_business_lane_status_is_final_cleanup_complete() -> None:
    text = LEGACY_CLEANUP_MATRIX_DOC.read_text(encoding="utf-8")

    assert "phase5_business_lane_status: final-cleanup-complete" in text


def test_current_state_docs_do_not_keep_stale_business_blockers() -> None:
    stale_matches = [
        f"{path.relative_to(REPO_ROOT)}:{token}"
        for path in CURRENT_STATE_ARCHITECTURE_DOCS
        for token in STALE_BUSINESS_BLOCKER_TOKENS
        if token in path.read_text(encoding="utf-8")
    ]

    assert stale_matches == []
