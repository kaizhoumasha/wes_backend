"""T1 北向 WMS 遗留清单的 T12 完成态守护。"""

from __future__ import annotations

import csv
from pathlib import Path

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


def test_northbound_wms_legacy_inventory_is_empty_and_keeps_stable_schema() -> None:
    with INVENTORY_PATH.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)

    assert tuple(reader.fieldnames or ()) == EXPECTED_FIELDS
    assert rows == []
