from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import BinContentSnapshot, BinContentSnapshotItem, BinContentSnapshotStatus
from src.app.resource.services.snapshot_service import ResourceSnapshotService


class RecordingSnapshotRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, _db: object, data: dict[str, Any]) -> BinContentSnapshot:
        self.created.append(data)
        return BinContentSnapshot(**data)


class RecordingSnapshotItemRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, _db: object, data: dict[str, Any]) -> BinContentSnapshotItem:
        self.created.append(data)
        return BinContentSnapshotItem(**data)


@pytest.mark.asyncio
async def test_record_empty_bin_snapshots_from_arrived_rack_writes_one_complete_snapshot_per_bin() -> None:
    snapshots = RecordingSnapshotRepo()
    items = RecordingSnapshotItemRepo()
    service = ResourceSnapshotService(snapshot_repo=snapshots, snapshot_item_repo=items)

    result = await service.record_empty_bin_snapshots_from_arrived_rack(
        SimpleNamespace(),
        rack_code="RACK-EMPTY-001",
        bin_mounts=[
            {"rack_slot_code": "A", "bin_code": "BIN-A"},
            {"rack_slot_code": "B", "bin_code": "BIN-B"},
            {"rack_slot_code": "C", "bin_code": "BIN-C"},
            {"rack_slot_code": "D", "bin_code": "BIN-D"},
        ],
        source_session_id=2001,
        source_event_id="wms-event-001",
        captured_at=datetime(2026, 5, 18, 9, 0, 0),
    )

    assert len(result) == 4
    assert [row["bin_code"] for row in snapshots.created] == ["BIN-A", "BIN-B", "BIN-C", "BIN-D"]
    assert {row["snapshot_status"] for row in snapshots.created} == {BinContentSnapshotStatus.COMPLETE.value}
    assert {row["snapshot_reason"] for row in snapshots.created} == {"EMPTY_RACK_ARRIVED"}
    assert {row["snapshot_group_key"] for row in snapshots.created} == {
        "EMPTY_RACK_ARRIVED:wms-event-001:RACK-EMPTY-001"
    }
    assert {row["source_session_id"] for row in snapshots.created} == {2001}
    assert items.created == []


@pytest.mark.asyncio
async def test_record_material_mounted_snapshot_writes_head_and_occupied_cell_item() -> None:
    snapshots = RecordingSnapshotRepo()
    items = RecordingSnapshotItemRepo()
    service = ResourceSnapshotService(snapshot_repo=snapshots, snapshot_item_repo=items)

    result = await service.record_material_mounted_snapshot(
        SimpleNamespace(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        pkg_code="PKG-001",
        material_code="620100L00-011-G",
        lot_code="8904936031",
        date_code="122625",
        qty_snapshot=7387,
        wms_inventory_id="INV-001",
        source_session_id=2001,
        source_event_id="CMD-PICK-001",
        captured_at=datetime(2026, 5, 18, 9, 5, 0),
    )

    assert result.bin_code == "BIN-001"
    assert snapshots.created[0]["snapshot_status"] == BinContentSnapshotStatus.COMPLETE.value
    assert snapshots.created[0]["snapshot_reason"] == "MATERIAL_MOUNTED"
    assert snapshots.created[0]["snapshot_group_key"] == "MATERIAL_MOUNTED:CMD-PICK-001"
    assert snapshots.created[0]["snapshot_hash"]
    assert items.created == [
        {
            "snapshot_id": snapshots.created[0]["snapshot_id"],
            "bin_cell_code": "BIN-001-4",
            "bin_cell_index": "4",
            "pkg_code": "PKG-001",
            "material_code": "620100L00-011-G",
            "lot_code": "8904936031",
            "date_code": "122625",
            "qty_snapshot": 7387,
            "wms_inventory_id": "INV-001",
        }
    ]


@pytest.mark.asyncio
async def test_record_snapshots_bounds_generated_keys_for_long_source_event_id() -> None:
    snapshots = RecordingSnapshotRepo()
    items = RecordingSnapshotItemRepo()
    service = ResourceSnapshotService(snapshot_repo=snapshots, snapshot_item_repo=items)
    source_event_id = "E" * 200

    await service.record_empty_bin_snapshots_from_arrived_rack(
        SimpleNamespace(),
        rack_code="RACK-LONG-SOURCE-EVENT",
        bin_mounts=[
            {"rack_slot_code": "A", "bin_code": "BIN-LONG-A"},
            {"rack_slot_code": "B", "bin_code": "BIN-LONG-B"},
        ],
        source_session_id=2001,
        source_event_id=source_event_id,
        captured_at=datetime(2026, 5, 18, 9, 0, 0),
    )
    await service.record_material_mounted_snapshot(
        SimpleNamespace(),
        bin_code="BIN-LONG-A",
        bin_cell_code="BIN-LONG-A-1",
        bin_cell_index="1",
        pkg_code="PKG-LONG-SOURCE-EVENT",
        material_code="620100L00-011-G",
        lot_code="8904936031",
        date_code="122625",
        qty_snapshot=7387,
        wms_inventory_id="INV-LONG-SOURCE-EVENT",
        source_session_id=2001,
        source_event_id=source_event_id,
        captured_at=datetime(2026, 5, 18, 9, 5, 0),
    )

    assert all(len(row["snapshot_id"]) <= 160 for row in snapshots.created)
    assert all(len(str(row["snapshot_group_key"])) <= 160 for row in snapshots.created)
    assert all(len(row["snapshot_id"]) <= 160 for row in items.created)
