from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.services.active_rack_snapshot_service import SmtActiveRackSnapshotService


class RecordingRackPlacementRepo:
    async def list_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[SimpleNamespace]:
        assert (workline_code, position_code) == ("WL-SMT-001", "SINGLE_LAYER_A")
        return [SimpleNamespace(rack_code="RACK-ACTIVE-001")]


class RecordingRackPlacementRepoForPosition:
    def __init__(self, expected_position_code: str) -> None:
        self.expected_position_code = expected_position_code

    async def list_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[SimpleNamespace]:
        assert (workline_code, position_code) == ("WL-SMT-001", self.expected_position_code)
        return [SimpleNamespace(rack_code="RACK-ACTIVE-001")]


class RecordingMultipleRackPlacementRepo:
    async def list_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[SimpleNamespace]:
        assert (workline_code, position_code) == ("WL-SMT-001", "SINGLE_LAYER_A")
        return [SimpleNamespace(rack_code="RACK-ACTIVE-001"), SimpleNamespace(rack_code="RACK-ACTIVE-002")]


class RecordingRackBinMountRepo:
    async def list_active_by_rack_code(self, _db: object, rack_code: str) -> list[SimpleNamespace]:
        assert rack_code == "RACK-ACTIVE-001"
        return [
            SimpleNamespace(rack_slot_code="A", bin_code="BIN-ACTIVE-A"),
            SimpleNamespace(rack_slot_code="B", bin_code="BIN-ACTIVE-B"),
            SimpleNamespace(rack_slot_code="C", bin_code="BIN-ACTIVE-C"),
            SimpleNamespace(rack_slot_code="D", bin_code="BIN-ACTIVE-D"),
        ]


class FailingRackBinMountRepo:
    async def list_active_by_rack_code(self, _db: object, rack_code: str) -> list[SimpleNamespace]:
        raise AssertionError(f"ambiguous placement should not select rack: {rack_code}")


class RecordingPartialRackBinMountRepo:
    async def list_active_by_rack_code(self, _db: object, rack_code: str) -> list[SimpleNamespace]:
        assert rack_code == "RACK-ACTIVE-001"
        return [
            SimpleNamespace(rack_slot_code="A", bin_code="BIN-ACTIVE-A"),
            SimpleNamespace(rack_slot_code="C", bin_code="BIN-ACTIVE-C"),
            SimpleNamespace(rack_slot_code="D", bin_code="BIN-ACTIVE-D"),
        ]


class RecordingBinMaterialMountRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes == ["BIN-ACTIVE-A", "BIN-ACTIVE-B", "BIN-ACTIVE-C", "BIN-ACTIVE-D"]
        return [
            SimpleNamespace(
                bin_cell_occupancy_id=7701,
                cell_stack_position=1,
                bin_code="BIN-ACTIVE-C",
                bin_cell_index="7",
                material_identity_key="MAT:620100L00-011-G:122625:8904936031",
                pkg_code="SVYU00125TP4LCR02_1",
                material_code="620100L00-011-G",
                lot_code="8904936031",
                date_code="122625",
                qty_snapshot=7387,
                reel_diameter="15inch",
                reel_thickness="20",
                wms_inventory_id="INV-OLD",
            ),
            SimpleNamespace(
                bin_cell_occupancy_id=7701,
                cell_stack_position=2,
                bin_code="BIN-ACTIVE-C",
                bin_cell_index="7",
                material_identity_key="MAT:620100L00-011-G:122625:8904936031",
                pkg_code="SVYU00125TP4LCR02_2",
                material_code="620100L00-011-G",
                lot_code="8904936031",
                date_code="122625",
                qty_snapshot=7000,
                reel_diameter="15inch",
                reel_thickness="20",
                wms_inventory_id="INV-NEW",
            ),
        ]


class RecordingBinCellOccupancyRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes == ["BIN-ACTIVE-A", "BIN-ACTIVE-B", "BIN-ACTIVE-C", "BIN-ACTIVE-D"]
        return [
            SimpleNamespace(
                id=7701,
                bin_code="BIN-ACTIVE-C",
                bin_cell_index="7",
                material_identity_key="MAT:620100L00-011-G:122625:8904936031",
                material_code="620100L00-011-G",
                lot_code="8904936031",
                date_code="122625",
                reel_count=2,
                used_depth_mm=40.0,
                capacity_depth_mm=60.0,
                remaining_depth_mm=20.0,
                occupancy_status="OCCUPIED",
            )
        ]


class RecordingDecimalBinCellOccupancyRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes == ["BIN-ACTIVE-A", "BIN-ACTIVE-B", "BIN-ACTIVE-C", "BIN-ACTIVE-D"]
        return [
            SimpleNamespace(
                id=7701,
                bin_code="BIN-ACTIVE-C",
                bin_cell_index="7",
                material_identity_key="MAT:620100L00-011-G:122625:8904936031",
                material_code="620100L00-011-G",
                lot_code="8904936031",
                date_code="122625",
                reel_count=2,
                used_depth_mm=Decimal("40.10"),
                capacity_depth_mm=Decimal("60.30"),
                remaining_depth_mm=Decimal("20.20"),
                occupancy_status="OCCUPIED",
            )
        ]


class RecordingNoOccupancyRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes == ["BIN-ACTIVE-A", "BIN-ACTIVE-C", "BIN-ACTIVE-D"]
        return []


class RecordingNoMaterialMountRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes == ["BIN-ACTIVE-A", "BIN-ACTIVE-C", "BIN-ACTIVE-D"]
        return []


class RecordingNoReservationRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes
        return []


class RecordingActiveReservationRepo:
    async def list_active_by_bin_codes(self, _db: object, bin_codes: list[str]) -> list[SimpleNamespace]:
        assert bin_codes == ["BIN-ACTIVE-A", "BIN-ACTIVE-B", "BIN-ACTIVE-C", "BIN-ACTIVE-D"]
        return [
            SimpleNamespace(
                session_id=42,
                pkg_code="PKG-PLANNED-001",
                bin_code="BIN-ACTIVE-A",
                bin_cell_index="7",
                metadata_json={"material_identity_key": "MAT:PLANNED:VENDOR:20260520:LOT-001"},
            )
        ]


class RecordingSessionRepo:
    async def get_latest_active_rack_template_session(
        self,
        _db: object,
        *,
        workline_id: int,
        rack_code: str,
    ) -> SimpleNamespace:
        assert workline_id == 1001
        assert rack_code == "RACK-ACTIVE-001"
        return SimpleNamespace(
            context_json={
                "active_bin_rack": {
                    "rack_id": "RACK-ACTIVE-001",
                    "rack_code": "RACK-ACTIVE-001",
                    "cells": [
                        _cell("A", "BIN-ACTIVE-A"),
                        _cell("B", "BIN-ACTIVE-B"),
                        _cell("C", "BIN-ACTIVE-C"),
                        _cell("D", "BIN-ACTIVE-D"),
                    ],
                }
            },
            ended_at=datetime(2026, 5, 18, 9, 0, 0),
        )


class RecordingSessionRepoWithStaleBinSnapshots:
    async def get_latest_active_rack_template_session(
        self,
        _db: object,
        *,
        workline_id: int,
        rack_code: str,
    ) -> SimpleNamespace:
        assert workline_id == 1001
        assert rack_code == "RACK-ACTIVE-001"
        stale_bins = [
            {
                "slot_code": slot,
                "bin_id": f"BIN-ACTIVE-{slot}",
                "status": "EMPTY_VERIFIED",
                "bin_execution_status": "EMPTY_VERIFIED",
                "usage": 0.0,
                "usage_snapshot": 0.0,
            }
            for slot in ("A", "B", "C", "D")
        ]
        return SimpleNamespace(
            context_json={
                "active_bin_rack": {
                    "rack_id": "RACK-ACTIVE-001",
                    "rack_code": "RACK-ACTIVE-001",
                    "cells": [
                        _cell("A", "BIN-ACTIVE-A"),
                        _cell("B", "BIN-ACTIVE-B"),
                        _cell("C", "BIN-ACTIVE-C"),
                        _cell("D", "BIN-ACTIVE-D"),
                    ],
                    "bins": stale_bins,
                    "bin_snapshots": stale_bins,
                }
            },
            ended_at=datetime(2026, 5, 18, 9, 0, 0),
        )


class RecordingSessionRepoWithStaleEmptyCell:
    async def get_latest_active_rack_template_session(
        self,
        _db: object,
        *,
        workline_id: int,
        rack_code: str,
    ) -> SimpleNamespace:
        assert workline_id == 1001
        assert rack_code == "RACK-ACTIVE-001"
        stale_cell = {
            **_cell("D", "BIN-ACTIVE-D"),
            "status": "OCCUPIED",
            "DateCode": "OLD-DC",
            "LotCode": "OLD-LC",
            "PkgID": "OLD-PKG",
            "HHPN": "OLD-HHPN",
            "Qty": 100,
            "reel_count": 1,
            "used_depth_mm": 20.0,
            "capacity_depth_mm": 60.0,
            "remaining_depth_mm": 40.0,
            "material_identity_key": "MAT:OLD-HHPN:OLD-MFR:OLD-DC:OLD-LC",
            "reels": [{"pkg_code": "OLD-PKG", "cell_stack_position": 1}],
            "reel_diameter": "15inch",
            "reel_thickness": "20",
            "wms_inventory_id": "INV-OLD-D",
        }
        return SimpleNamespace(
            context_json={
                "active_bin_rack": {
                    "rack_id": "RACK-ACTIVE-001",
                    "rack_code": "RACK-ACTIVE-001",
                    "cells": [
                        _cell("A", "BIN-ACTIVE-A"),
                        _cell("B", "BIN-ACTIVE-B"),
                        _cell("C", "BIN-ACTIVE-C"),
                        stale_cell,
                    ],
                }
            },
            ended_at=datetime(2026, 5, 18, 9, 0, 0),
        )


class RecordingSessionRepoWithLockedEmptyCell:
    async def get_latest_active_rack_template_session(
        self,
        _db: object,
        *,
        workline_id: int,
        rack_code: str,
    ) -> SimpleNamespace:
        assert workline_id == 1001
        assert rack_code == "RACK-ACTIVE-001"
        locked_cell = {
            **_cell("D", "BIN-ACTIVE-D"),
            "status": "LOCKED",
            "DateCode": "OLD-DC",
            "LotCode": "OLD-LC",
            "PkgID": "OLD-PKG",
            "HHPN": "OLD-HHPN",
            "material_identity_key": "MAT:OLD-HHPN:OLD-MFR:OLD-DC:OLD-LC",
            "reels": [{"pkg_code": "OLD-PKG", "cell_stack_position": 1}],
        }
        return SimpleNamespace(
            context_json={
                "active_bin_rack": {
                    "rack_id": "RACK-ACTIVE-001",
                    "rack_code": "RACK-ACTIVE-001",
                    "cells": [
                        _cell("A", "BIN-ACTIVE-A"),
                        _cell("B", "BIN-ACTIVE-B"),
                        _cell("C", "BIN-ACTIVE-C"),
                        locked_cell,
                    ],
                }
            },
            ended_at=datetime(2026, 5, 18, 9, 0, 0),
        )


def _cell(slot: str, bin_code: str) -> dict[str, Any]:
    return {
        "rack_id": "RACK-ACTIVE-001",
        "rack_slot_code": slot,
        "rack_slot_location_code": f"RACK-ACTIVE-001-1{slot}-0",
        "bin_id": bin_code,
        "bin_type": "3格箱",
        "bin_cell_location": f"{bin_code}-7",
        "bin_cell_index": "7",
        "status": "EMPTY",
    }


@pytest.mark.asyncio
async def test_get_active_bin_rack_restores_snapshot_from_projection_and_last_session() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepo(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is not None
    cells = snapshot["cells"]
    cell_c = next(cell for cell in cells if cell["bin_id"] == "BIN-ACTIVE-C")
    assert cell_c["status"] == "OCCUPIED"
    assert cell_c["DateCode"] == "122625"
    assert cell_c["LotCode"] == "8904936031"
    assert cell_c["PkgID"] == "SVYU00125TP4LCR02_2"
    assert cell_c["reel_count"] == 2
    assert cell_c["used_depth_mm"] == 40.0
    assert cell_c["remaining_depth_mm"] == 20.0
    assert cell_c["reels"] == [
        {
            "pkg_code": "SVYU00125TP4LCR02_2",
            "cell_stack_position": 2,
            "reel_diameter": "15inch",
            "reel_thickness": "20",
            "qty_snapshot": 7000,
            "wms_inventory_id": "INV-NEW",
        },
        {
            "pkg_code": "SVYU00125TP4LCR02_1",
            "cell_stack_position": 1,
            "reel_diameter": "15inch",
            "reel_thickness": "20",
            "qty_snapshot": 7387,
            "wms_inventory_id": "INV-OLD",
        },
    ]
    cell_d = next(cell for cell in cells if cell["bin_id"] == "BIN-ACTIVE-D")
    assert cell_d["status"] == "EMPTY"


@pytest.mark.asyncio
async def test_get_active_bin_rack_preserves_decimal_depth_values() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingDecimalBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepo(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is not None
    cell_c = next(cell for cell in snapshot["cells"] if cell["bin_id"] == "BIN-ACTIVE-C")
    assert cell_c["used_depth_mm"] == Decimal("40.10")
    assert cell_c["capacity_depth_mm"] == Decimal("60.30")
    assert cell_c["remaining_depth_mm"] == Decimal("20.20")
    assert all(
        not isinstance(cell_c[key], float) for key in ("used_depth_mm", "capacity_depth_mm", "remaining_depth_mm")
    )


@pytest.mark.asyncio
async def test_get_active_bin_rack_uses_rack_operation_work_position_code() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepoForPosition("SINGLE_LAYER_B"),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepo(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={"rack_operation": {"work_position_code": "SINGLE_LAYER_B"}},
    )

    assert snapshot is not None
    assert snapshot["rack_code"] == "RACK-ACTIVE-001"


@pytest.mark.asyncio
async def test_get_active_bin_rack_removes_stale_top_level_bin_snapshots_after_overlay() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepoWithStaleBinSnapshots(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is not None
    assert "bins" not in snapshot
    assert "bin_snapshots" not in snapshot
    cell_c = next(cell for cell in snapshot["cells"] if cell["bin_id"] == "BIN-ACTIVE-C")
    assert cell_c["status"] == "OCCUPIED"
    assert cell_c["used_depth_mm"] == 40.0


@pytest.mark.asyncio
async def test_get_active_bin_rack_rejects_template_with_bins_missing_active_mount() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingPartialRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingNoOccupancyRepo(),
        bin_material_mount_repo=RecordingNoMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepo(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is None


@pytest.mark.asyncio
async def test_get_active_bin_rack_returns_none_when_position_has_multiple_active_placements() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingMultipleRackPlacementRepo(),
        rack_bin_mount_repo=FailingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingNoOccupancyRepo(),
        bin_material_mount_repo=RecordingNoMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepo(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is None


@pytest.mark.asyncio
async def test_get_active_bin_rack_clears_template_cell_without_active_occupancy() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepoWithStaleEmptyCell(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is not None
    cell_d = next(cell for cell in snapshot["cells"] if cell["bin_id"] == "BIN-ACTIVE-D")
    assert cell_d["status"] == "EMPTY"
    assert cell_d["capacity_depth_mm"] == 60.0
    for stale_key in (
        "DateCode",
        "LotCode",
        "PkgID",
        "HHPN",
        "Qty",
        "reel_count",
        "used_depth_mm",
        "remaining_depth_mm",
        "material_identity_key",
        "reels",
        "reel_diameter",
        "reel_thickness",
        "wms_inventory_id",
    ):
        assert stale_key not in cell_d


@pytest.mark.asyncio
async def test_get_active_bin_rack_preserves_locked_template_cell_without_active_occupancy() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingNoReservationRepo(),
        session_repo=RecordingSessionRepoWithLockedEmptyCell(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is not None
    cell_d = next(cell for cell in snapshot["cells"] if cell["bin_id"] == "BIN-ACTIVE-D")
    assert cell_d["status"] == "LOCKED"
    assert cell_d["bin_code"] == "BIN-ACTIVE-D"
    assert cell_d["bin_cell_index"] == "7"
    for stale_key in (
        "DateCode",
        "LotCode",
        "PkgID",
        "HHPN",
        "material_identity_key",
        "reels",
    ):
        assert stale_key not in cell_d


@pytest.mark.asyncio
async def test_get_active_bin_rack_marks_planned_reservation_cell_locked() -> None:
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_bin_mount_repo=RecordingRackBinMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_reservation_repo=RecordingActiveReservationRepo(),
        session_repo=RecordingSessionRepo(),
    )

    snapshot = await service.get_active_bin_rack(
        SimpleNamespace(),
        workline=SimpleNamespace(id=1001, line_code="WL-SMT-001"),
        context={},
    )

    assert snapshot is not None
    cell_a = next(cell for cell in snapshot["cells"] if cell["bin_id"] == "BIN-ACTIVE-A")
    assert cell_a["status"] == "LOCKED"
    assert cell_a["locked"] is True
    assert cell_a["reservation_status"] == "PLANNED"
    assert cell_a["reservation_session_id"] == 42
    assert cell_a["reserved_pkg_code"] == "PKG-PLANNED-001"
    assert cell_a["reserved_material_identity_key"] == "MAT:PLANNED:VENDOR:20260520:LOT-001"

    cell_c = next(cell for cell in snapshot["cells"] if cell["bin_id"] == "BIN-ACTIVE-C")
    assert cell_c["status"] == "OCCUPIED"
