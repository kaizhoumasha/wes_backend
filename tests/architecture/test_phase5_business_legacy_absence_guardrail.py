"""State-aware absence guardrail for Phase5 business destructive cleanup."""

from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest

from scripts.check_phase5_business_destructive_cleanup_gate import (
    LEDGER_PATH,
    STRICT_DISPOSITIONS,
    strict_reference_violations,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TECHNICAL_LANE_FORBIDDEN_MODULES = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
)


def _ledger_rows() -> list[dict[str, str]]:
    with (REPO_ROOT / LEDGER_PATH).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.mark.parametrize("module_name", TECHNICAL_LANE_FORBIDDEN_MODULES)
def test_legacy_plugin_runtime_surfaces_stay_absent(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_closed_business_rows_have_no_pending_disposition() -> None:
    rows = _ledger_rows()

    assert all(row["cleanup_disposition"] != "pending" for row in rows)
    assert strict_reference_violations(REPO_ROOT, rows) == ()


def test_strict_business_rows_have_no_runtime_reference_backflow() -> None:
    rows = _ledger_rows()
    strict_rows = [row for row in rows if row["cleanup_disposition"] in STRICT_DISPOSITIONS]

    assert strict_reference_violations(REPO_ROOT, rows) == ()
    assert all(row["reference_scan_status"] != "pending" for row in strict_rows)
