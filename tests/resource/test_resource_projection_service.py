from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import (
    BinMaterialMount,
    BinMaterialMountStatus,
    RackBinMount,
    RackBinMountStatus,
    RackKind,
    RackPlacement,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
)
from src.app.resource.services import ResourceProjectionStatus
from src.app.resource.services.projection_service import ResourceProjectionService


class RecordingStateEventRepo:
    def __init__(self, existing: ResourceStateEvent | None = None) -> None:
        self.existing = existing
        self.created: list[dict[str, Any]] = []

    async def get_by_idempotency_key(self, _db: object, idempotency_key: str) -> ResourceStateEvent | None:
        assert idempotency_key
        return self.existing

    async def create(self, _db: object, data: dict[str, Any]) -> ResourceStateEvent:
        self.created.append(data)
        return ResourceStateEvent(**data)


class RecordingRackPlacementRepo:
    def __init__(
        self,
        *,
        active_by_rack: RackPlacement | None = None,
        active_by_position: RackPlacement | None = None,
    ) -> None:
        self.active_by_rack = active_by_rack
        self.active_by_position = active_by_position
        self.created: list[dict[str, Any]] = []

    async def get_active_by_rack_code(self, _db: object, rack_code: str) -> RackPlacement | None:
        assert rack_code == "RACK-001"
        return self.active_by_rack

    async def get_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> RackPlacement | None:
        assert (workline_code, position_code) == ("SMT_SORTER_01", "SINGLE_LAYER_A")
        return self.active_by_position

    async def create(self, _db: object, data: dict[str, Any]) -> RackPlacement:
        self.created.append(data)
        return RackPlacement(**data)


class RecordingRackPositionService:
    async def require_enabled_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> SimpleNamespace:
        assert (workline_code, position_code, rack_kind) == (
            "SMT_SORTER_01",
            "SINGLE_LAYER_A",
            RackKind.SINGLE_LAYER,
        )
        return SimpleNamespace(
            workline_id=1001,
            workline_code=workline_code,
            position_code=position_code,
            position_role="OUTPUT_BUFFER",
            logic_location_code="SMT_SORTER_01_SINGLE_A",
            external_location_code="RCS-SINGLE-A",
        )


class RecordingBinMaterialMountRepo:
    def __init__(
        self,
        *,
        active_by_cell: BinMaterialMount | None = None,
        active_by_pkg: BinMaterialMount | None = None,
        active_by_wms_inventory: BinMaterialMount | None = None,
        active_by_material_identity: BinMaterialMount | None = None,
    ) -> None:
        self.active_by_cell = active_by_cell
        self.active_by_pkg = active_by_pkg
        self.active_by_wms_inventory = active_by_wms_inventory
        self.active_by_material_identity = active_by_material_identity
        self.material_identity_lookups: list[str] = []
        self.created: list[dict[str, Any]] = []

    async def get_active_by_bin_cell(
        self, _db: object, *, bin_code: str, bin_cell_index: str
    ) -> BinMaterialMount | None:
        assert (bin_code, bin_cell_index) == ("BIN-001", "4")
        return self.active_by_cell

    async def get_active_by_pkg_code(self, _db: object, pkg_code: str) -> BinMaterialMount | None:
        assert pkg_code == "PKG-001"
        return self.active_by_pkg

    async def get_active_by_wms_inventory_id(self, _db: object, wms_inventory_id: str) -> BinMaterialMount | None:
        assert wms_inventory_id == "INV-001"
        return self.active_by_wms_inventory

    async def get_active_by_material_identity(
        self,
        _db: object,
        material_identity_key: str,
    ) -> list[BinMaterialMount]:
        self.material_identity_lookups.append(material_identity_key)
        if self.active_by_material_identity is None:
            return []
        return [self.active_by_material_identity]

    async def create(self, _db: object, data: dict[str, Any]) -> BinMaterialMount:
        self.created.append(data)
        return BinMaterialMount(**data)


class RecordingRackBinMountRepo:
    def __init__(
        self,
        *,
        active_by_slot: RackBinMount | None = None,
        active_by_bin: RackBinMount | None = None,
        active_by_slot_map: dict[tuple[str, str], RackBinMount] | None = None,
        active_by_bin_map: dict[str, RackBinMount] | None = None,
    ) -> None:
        self.active_by_slot = active_by_slot
        self.active_by_bin = active_by_bin
        self.active_by_slot_map = active_by_slot_map
        self.active_by_bin_map = active_by_bin_map
        self.created: list[dict[str, Any]] = []

    async def get_active_by_rack_slot(
        self,
        _db: object,
        *,
        rack_code: str,
        rack_slot_code: str,
    ) -> RackBinMount | None:
        if self.active_by_slot_map is not None:
            return self.active_by_slot_map.get((rack_code, rack_slot_code))
        assert rack_code == "RACK-001"
        return self.active_by_slot

    async def get_active_by_bin_code(self, _db: object, bin_code: str) -> RackBinMount | None:
        if self.active_by_bin_map is not None:
            return self.active_by_bin_map.get(bin_code)
        return self.active_by_bin

    async def create(self, _db: object, data: dict[str, Any]) -> RackBinMount:
        self.created.append(data)
        return RackBinMount(**data)


class RecordingRuntimeHoldCreator:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_for_resource_reconciliation(self, _db: object, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id=8801, **kwargs)


def _mount(**overrides: Any) -> BinMaterialMount:
    values: dict[str, Any] = {
        "bin_code": "BIN-001",
        "bin_cell_code": "BIN-001-4",
        "bin_cell_index": "4",
        "material_identity_key": "MAT:620100L00-011-G:122625:8904936031",
        "pkg_code": "PKG-OLD",
        "material_code": "620100L00-011-G",
        "wms_inventory_id": "INV-OLD",
        "mount_status": BinMaterialMountStatus.OCCUPIED,
        "source_system": ResourceSourceSystem.WES_RUNTIME,
        "source_event_id": "old-event",
        "started_at": datetime(2026, 5, 18, 8, 0, 0),
    }
    values.update(overrides)
    return BinMaterialMount(**values)


def _rack_bin_mount(**overrides: Any) -> RackBinMount:
    values: dict[str, Any] = {
        "rack_code": "RACK-001",
        "rack_slot_code": "A",
        "bin_code": "BIN-OLD",
        "mount_status": RackBinMountStatus.MOUNTED,
        "source_system": ResourceSourceSystem.WMS,
        "source_event_id": "old-event",
        "started_at": datetime(2026, 5, 18, 8, 0, 0),
    }
    values.update(overrides)
    return RackBinMount(**values)


def test_event_code_distinguishes_long_source_event_ids_by_resource_code() -> None:
    source_event_id = "WMS-RACK-ARRIVED-" + ("X" * 190)

    first = ResourceProjectionService._event_code(
        event_type=ResourceStateEventType.BIN_MOUNTED,
        source_system=ResourceSourceSystem.WMS,
        source_event_id=source_event_id,
        resource_code="RACK-001",
    )
    second = ResourceProjectionService._event_code(
        event_type=ResourceStateEventType.BIN_MOUNTED,
        source_system=ResourceSourceSystem.WMS,
        source_event_id=source_event_id,
        resource_code="RACK-002",
    )

    assert first != second
    assert len(first) <= 160
    assert len(second) <= 160
    assert first.startswith("BIN_MOUNTED:WMS:")


@pytest.mark.asyncio
async def test_record_rack_arrived_at_workline_position_projects_active_placement() -> None:
    events = RecordingStateEventRepo()
    placements = RecordingRackPlacementRepo()
    service = ResourceProjectionService(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-001",
        idempotency_key="RACK_ARRIVED:wms-event-001:RACK-001",
        occurred_at=datetime(2026, 5, 18, 9, 0, 0),
        trace_id="trace-001",
        session_id="2001",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert events.created[0]["idempotency_key"] == "RACK_ARRIVED:wms-event-001:RACK-001"
    assert events.created[0]["workline_code"] == "SMT_SORTER_01"
    assert events.created[0]["position_code"] == "SINGLE_LAYER_A"
    assert placements.created[0]["workline_code"] == "SMT_SORTER_01"
    assert placements.created[0]["position_code"] == "SINGLE_LAYER_A"
    assert placements.created[0]["logic_location_code"] == "SMT_SORTER_01_SINGLE_A"


@pytest.mark.asyncio
async def test_record_material_mounted_to_bin_cell_projects_active_mount() -> None:
    events = RecordingStateEventRepo()
    mounts = RecordingBinMaterialMountRepo()
    service = ResourceProjectionService(state_event_repo=events, bin_material_mount_repo=mounts)

    result = await service.record_material_mounted_to_bin_cell(
        SimpleNamespace(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-001",
        material_code="620100L00-011-G",
        lot_code="8904936031",
        date_code="122625",
        wms_inventory_id="INV-001",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-PICK-001",
        idempotency_key="MATERIAL_MOUNTED:CMD-PICK-001:PKG-001:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        session_id="2001",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert mounts.material_identity_lookups == ["MAT:620100L00-011-G:122625:8904936031"]
    assert mounts.created[0]["bin_code"] == "BIN-001"
    assert mounts.created[0]["pkg_code"] == "PKG-001"
    assert mounts.created[0]["wms_inventory_id"] == "INV-001"
    assert mounts.created[0]["mount_status"] == BinMaterialMountStatus.OCCUPIED.value
    assert events.created[0]["event_type"] == "MATERIAL_MOUNTED"


@pytest.mark.asyncio
async def test_record_material_mounted_to_occupied_bin_cell_creates_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(active_by_cell=_mount())
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_mounted_to_bin_cell(
        SimpleNamespace(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-001",
        material_code="620100L00-011-G",
        lot_code="8904936031",
        date_code="122625",
        wms_inventory_id="INV-001",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-PICK-001",
        idempotency_key="MATERIAL_MOUNTED:CMD-PICK-001:PKG-001:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        session_id="2001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="smt_classifier",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_CELL_MATERIAL_MOUNT_CONFLICT"
    assert mounts.created == []
    assert runtime_holds.created[0]["source_reason"] == "BIN_CELL_MATERIAL_MOUNT_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_pkg_code"] == "PKG-OLD"


@pytest.mark.asyncio
async def test_record_material_mounted_to_duplicate_material_identity_creates_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(
        active_by_material_identity=_mount(
            bin_code="BIN-OLD",
            bin_cell_code="BIN-OLD-5",
            bin_cell_index="5",
            pkg_code="PKG-OLD",
        )
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_mounted_to_bin_cell(
        SimpleNamespace(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-001",
        material_code="620100L00-011-G",
        lot_code="8904936031",
        date_code="122625",
        wms_inventory_id="INV-001",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-PICK-001",
        idempotency_key="MATERIAL_MOUNTED:CMD-PICK-001:PKG-001:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        session_id="2001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="smt_classifier",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_IDENTITY_MOUNT_CONFLICT"
    assert mounts.created == []
    assert runtime_holds.created[0]["source_reason"] == "MATERIAL_IDENTITY_MOUNT_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_pkg_code"] == "PKG-OLD"


@pytest.mark.asyncio
async def test_record_bin_mounted_to_rack_conflict_creates_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    rack_bins = RecordingRackBinMountRepo(active_by_slot=_rack_bin_mount())
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_bin_mount_repo=rack_bins,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_bin_mounted_to_rack(
        SimpleNamespace(),
        rack_code="RACK-001",
        bin_mounts=[{"rack_slot_code": "A", "bin_code": "BIN-001"}],
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-MOUNTED-001",
        idempotency_key="BIN_MOUNTED:RACK-001:WMS-BIN-MOUNTED-001",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        session_id="2001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="smt_classifier",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "RACK_BIN_MOUNT_CONFLICT"
    assert rack_bins.created == []
    assert runtime_holds.created[0]["source_reason"] == "RACK_BIN_MOUNT_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_slot_bin_code"] == "BIN-OLD"


@pytest.mark.asyncio
async def test_record_bin_mounted_to_rack_conflict_on_later_mount_does_not_create_partial_projection() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    rack_bins = RecordingRackBinMountRepo(
        active_by_slot_map={
            ("RACK-001", "B"): _rack_bin_mount(rack_slot_code="B", bin_code="BIN-OLD"),
        }
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_bin_mount_repo=rack_bins,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_bin_mounted_to_rack(
        SimpleNamespace(),
        rack_code="RACK-001",
        bin_mounts=[
            {"rack_slot_code": "A", "bin_code": "BIN-001"},
            {"rack_slot_code": "B", "bin_code": "BIN-002"},
        ],
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-MOUNTED-001",
        idempotency_key="BIN_MOUNTED:RACK-001:WMS-BIN-MOUNTED-001",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        session_id="2001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="smt_classifier",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "RACK_BIN_MOUNT_CONFLICT"
    assert rack_bins.created == []
    assert runtime_holds.created[0]["evidence"]["rack_slot_code"] == "B"
