"""北向 WMS operation 的 target handoff 合同。"""

from __future__ import annotations

import csv
from pathlib import Path

from src.app.wms_integration.operation_registry import WMS_OPERATIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO_ROOT / "docs" / "architecture" / "northbound-wms-operation-inventory.csv"
EXPECTED_FIELDS = (
    "entry_id",
    "category",
    "source_path",
    "legacy_identity",
    "target_operation_identity",
    "disposition",
    "owner",
    "removal_gate",
    "notes",
)


E03_E07 = {
    "wms.inventory.confirm_inbound@v1",
    "wms.fulfillment.notify_pkg_binding@v1",
}


def _rows() -> list[dict[str, str]]:
    with INVENTORY_PATH.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)

    assert tuple(reader.fieldnames or ()) == EXPECTED_FIELDS
    return rows


def test_northbound_wms_inventory_has_no_unresolved_entry_and_every_owner_is_concrete() -> None:
    rows = _rows()

    assert len(rows) == 36
    assert len({row["entry_id"] for row in rows}) == len(rows)
    assert {row["disposition"] for row in rows} == {"RETAIN", "SWITCH", "DELETE → NONE"}
    assert all("UNRESOLVED" not in row.values() for row in rows)
    for row in rows:
        if row["disposition"] == "DELETE → NONE":
            assert row["owner"] == row["target_operation_identity"] == "NONE"
            continue
        owner_path, owner_symbol = row["owner"].split(":", maxsplit=1)
        assert (REPO_ROOT / owner_path).is_file()
        assert owner_symbol in (REPO_ROOT / owner_path).read_text(encoding="utf-8")


def test_only_e03_e07_switch_from_the_legacy_provider_registry() -> None:
    provider_rows = [row for row in _rows() if row["category"] == "wms_provider"]
    expected = {operation.identity for operation in WMS_OPERATIONS}

    assert len(provider_rows) == 29
    assert {row["legacy_identity"] for row in provider_rows} == expected
    assert {row["legacy_identity"] for row in provider_rows if row["disposition"] == "SWITCH"} == E03_E07
    assert {
        row["legacy_identity"] for row in provider_rows if row["disposition"] == "DELETE → NONE"
    } == expected - E03_E07


def test_deferred_manual_or_automatic_plugins_are_absent() -> None:
    assert not (REPO_ROOT / "workline_plugins/manual_bin_processing").exists()
    assert not (REPO_ROOT / "workline_plugins/automatic_putaway").exists()
    assert not (REPO_ROOT / "workline_plugins/automatic_picking").exists()
