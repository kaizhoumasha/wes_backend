"""State-aware absence guardrail for Business legacy absence."""

from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest

from scripts.check_business_legacy_absence_gate import (
    LEDGER_PATH,
    STRICT_DISPOSITIONS,
    strict_reference_violations,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = REPO_ROOT / "src"


def _token(*parts: str) -> str:
    return "".join(parts)


_HANDLING_QUEUE_MEMBERSHIP_MODULE = _token("bin", "_", "transit", "_", "membership")
_HANDLING_QUEUE_MEMBERSHIP_TABLE = _token("bin", "_", "transit", "_", "memberships")
LEGACY_RUNTIME_FORBIDDEN_MODULES = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
    f"src.app.handling.models.{_HANDLING_QUEUE_MEMBERSHIP_MODULE}",
    f"src.app.handling.repositories.{_HANDLING_QUEUE_MEMBERSHIP_MODULE}_repository",
    f"src.app.handling.services.{_HANDLING_QUEUE_MEMBERSHIP_MODULE}_service",
)
LEGACY_RUNTIME_FORBIDDEN_TEXT = (
    _token("Bin", "Transit", "Membership"),
    _token("Bin", "Transit", "Queue"),
    _HANDLING_QUEUE_MEMBERSHIP_TABLE,
)


def _ledger_rows() -> list[dict[str, str]]:
    with (REPO_ROOT / LEDGER_PATH).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.mark.parametrize("module_name", LEGACY_RUNTIME_FORBIDDEN_MODULES)
def test_legacy_plugin_runtime_surfaces_stay_absent(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_bin_transit_runtime_surfaces_stay_absent_from_production_source() -> None:
    offenders: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(forbidden in text for forbidden in LEGACY_RUNTIME_FORBIDDEN_TEXT):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_closed_business_rows_have_no_pending_disposition() -> None:
    rows = _ledger_rows()

    assert all(row["cleanup_disposition"] != "pending" for row in rows)
    assert strict_reference_violations(REPO_ROOT, rows) == ()


def test_strict_business_rows_have_no_runtime_reference_backflow() -> None:
    rows = _ledger_rows()
    strict_rows = [row for row in rows if row["cleanup_disposition"] in STRICT_DISPOSITIONS]

    assert strict_reference_violations(REPO_ROOT, rows) == ()
    assert all(row["reference_scan_status"] != "pending" for row in strict_rows)
