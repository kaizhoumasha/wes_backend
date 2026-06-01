from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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
    RackPlacementStatus,
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
        active_by_position_list: list[RackPlacement] | None = None,
    ) -> None:
        self.active_by_rack = active_by_rack
        self.active_by_position = active_by_position
        self.active_by_position_list = active_by_position_list
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[int, dict[str, Any]]] = []

    async def get_active_by_rack_code(self, _db: object, rack_code: str) -> RackPlacement | None:
        if self.active_by_rack is not None and self.active_by_rack.rack_code == rack_code:
            return self.active_by_rack
        return None

    async def list_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[RackPlacement]:
        assert (workline_code, position_code) == ("SMT_SORTER_01", "SINGLE_LAYER_A")
        if self.active_by_position_list is not None:
            return self.active_by_position_list
        return [self.active_by_position] if self.active_by_position is not None else []

    async def count_active_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> int:
        return len(
            await self.list_active_by_workline_position(
                _db,
                workline_code=workline_code,
                position_code=position_code,
            )
        )

    async def create(self, _db: object, data: dict[str, Any]) -> RackPlacement:
        self.created.append(data)
        return RackPlacement(**data)

    async def update(self, _db: object, id: int, data: dict[str, Any]) -> RackPlacement | None:
        self.updated.append((id, data))
        candidates = [
            placement
            for placement in [
                self.active_by_rack,
                self.active_by_position,
                *(self.active_by_position_list or []),
            ]
            if placement is not None and getattr(placement, "id", None) == id
        ]
        if not candidates:
            return None
        placement = candidates[0]
        for key, value in data.items():
            setattr(placement, key, value)
        return placement


class RecordingRackPositionService:
    def __init__(self, *, capacity: int = 1, enabled: bool = True) -> None:
        self.capacity = capacity
        self.enabled = enabled
        self.locked_calls: list[tuple[str, str, RackKind]] = []
        self.locked_capacity_calls: list[tuple[str, str, RackKind]] = []
        self.regular_calls: list[tuple[str, str, RackKind]] = []

    async def require_enabled_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> SimpleNamespace:
        self.regular_calls.append((workline_code, position_code, rack_kind))
        raise AssertionError("projection must use locked position lookup")

    async def require_enabled_position_for_update(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> SimpleNamespace:
        self.locked_calls.append((workline_code, position_code, rack_kind))
        raise AssertionError("projection must use locked capacity lookup")

    async def require_position_capacity_for_update(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> tuple[SimpleNamespace, int]:
        self.locked_capacity_calls.append((workline_code, position_code, rack_kind))
        assert (workline_code, position_code, rack_kind) == (
            "SMT_SORTER_01",
            "SINGLE_LAYER_A",
            RackKind.SINGLE_LAYER,
        )
        if not self.enabled:
            raise ValueError("workline rack position disabled: SMT_SORTER_01/SINGLE_LAYER_A")
        position = SimpleNamespace(
            workline_id=1001,
            workline_code=workline_code,
            position_code=position_code,
            position_role="SMT_SORTER_STATION",
            capacity=self.capacity,
            logic_location_code="SMT_SORTER_01_SINGLE_A",
            external_location_code="RCS-SINGLE-A",
        )
        return position, self.capacity


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


class RecordingBinCellOccupancyRepo:
    def __init__(
        self,
        *,
        active_by_cell: SimpleNamespace | None = None,
        active_by_material_identity: list[SimpleNamespace] | None = None,
    ) -> None:
        self.active_by_cell = active_by_cell
        self.active_by_material_identity = active_by_material_identity or []
        self.created: list[dict[str, Any]] = []
        self.saved: list[dict[str, Any]] = []

    async def get_active_by_bin_cell(
        self,
        _db: object,
        *,
        bin_code: str,
        bin_cell_index: str,
    ) -> SimpleNamespace | None:
        assert (bin_code, bin_cell_index) == ("BIN-001", "4")
        return self.active_by_cell

    async def list_active_by_material_identity(
        self,
        _db: object,
        material_identity_key: str,
    ) -> list[SimpleNamespace]:
        assert material_identity_key.startswith("MAT:620100L00-011-G:")
        return self.active_by_material_identity

    async def create(self, _db: object, data: dict[str, Any]) -> SimpleNamespace:
        self.created.append(data)
        return SimpleNamespace(id=7701, **data)

    async def save(self, _db: object, occupancy: SimpleNamespace) -> SimpleNamespace:
        self.saved.append(dict(vars(occupancy)))
        return occupancy


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


class RecordingResourceSnapshotService:
    def __init__(self) -> None:
        self.empty_rack_calls: list[dict[str, Any]] = []
        self.material_calls: list[dict[str, Any]] = []

    async def record_empty_bin_snapshots_from_arrived_rack(self, _db: object, **kwargs: Any) -> list[SimpleNamespace]:
        self.empty_rack_calls.append(kwargs)
        return [SimpleNamespace(id=index + 1) for index, _ in enumerate(kwargs["bin_mounts"])]

    async def record_material_mounted_snapshot(self, _db: object, **kwargs: Any) -> SimpleNamespace:
        self.material_calls.append(kwargs)
        return SimpleNamespace(id=9901, **kwargs)


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


def _occupancy(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 7701,
        "bin_code": "BIN-001",
        "bin_cell_code": "BIN-001-4",
        "bin_cell_index": "4",
        "material_identity_key": "MAT:620100L00-011-G:122625:8904936031",
        "material_code": "620100L00-011-G",
        "lot_code": "8904936031",
        "date_code": "122625",
        "reel_count": 1,
        "used_depth_mm": 2.5,
        "capacity_depth_mm": 5.0,
        "remaining_depth_mm": 2.5,
        "occupancy_status": "OCCUPIED",
        "source_system": ResourceSourceSystem.WES_RUNTIME,
        "source_event_id": "old-event",
        "started_at": datetime(2026, 5, 18, 8, 0, 0),
        "ended_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


def _rack_placement(**overrides: Any) -> RackPlacement:
    values: dict[str, Any] = {
        "id": 7101,
        "rack_code": "RACK-001",
        "rack_kind": RackKind.SINGLE_LAYER,
        "location_code": "SMT_SORTER_01_SINGLE_A",
        "workline_id": 1001,
        "workline_code": "SMT_SORTER_01",
        "position_code": "SINGLE_LAYER_A",
        "position_role": "SMT_SORTER_STATION",
        "logic_location_code": "SMT_SORTER_01_SINGLE_A",
        "external_location_code": "RCS-SINGLE-A",
        "source_system": ResourceSourceSystem.WMS,
        "source_event_id": "old-event",
        "started_at": datetime(2026, 5, 18, 8, 0, 0),
        "ended_at": None,
    }
    values.update(overrides)
    return RackPlacement(**values)


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
    positions = RecordingRackPositionService()
    service = ResourceProjectionService(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=positions,
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
    assert positions.locked_capacity_calls == [("SMT_SORTER_01", "SINGLE_LAYER_A", RackKind.SINGLE_LAYER)]
    assert positions.locked_calls == []
    assert positions.regular_calls == []


@pytest.mark.asyncio
async def test_record_rack_arrived_allows_second_rack_when_position_capacity_two() -> None:
    placements = RecordingRackPlacementRepo(active_by_position_list=[_rack_placement()])
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=2),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-002",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-002",
        idempotency_key="RACK_ARRIVED:wms-event-002:RACK-002",
        occurred_at=datetime(2026, 5, 18, 9, 1, 0),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert placements.created[0]["rack_code"] == "RACK-002"
    assert placements.created[0]["position_code"] == "SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_record_rack_arrived_reconciles_when_capacity_exhausted() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingRackPlacementRepo(active_by_position_list=[_rack_placement()])
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-002",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-002",
        idempotency_key="RACK_ARRIVED:wms-event-002:RACK-002",
        occurred_at=datetime(2026, 5, 18, 9, 1, 0),
        workline_id=1001,
        workline_session_id=2001,
        trace_id="trace-002",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "WORKLINE_POSITION_CAPACITY_EXHAUSTED"
    assert placements.created == []
    assert runtime_holds.created[0]["source_reason"] == "WORKLINE_POSITION_CAPACITY_EXHAUSTED"


@pytest.mark.asyncio
async def test_record_rack_arrived_releases_declared_old_rack_before_capacity_check() -> None:
    old_placement = _rack_placement(id=7101, rack_code="RACK-OLD")
    placements = RecordingRackPlacementRepo(active_by_position_list=[old_placement])
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-NEW",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-replace-001",
        idempotency_key="RACK_ARRIVED:wms-event-replace-001:RACK-NEW",
        occurred_at=datetime(2026, 5, 18, 9, 1, 0),
        released_rack_codes=["RACK-OLD"],
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert placements.updated == [
        (
            7101,
            {
                "placement_status": RackPlacementStatus.DEPARTED.value,
                "ended_at": datetime(2026, 5, 18, 9, 1, 0),
            },
        )
    ]
    assert old_placement.placement_status == RackPlacementStatus.DEPARTED.value
    assert placements.created[0]["rack_code"] == "RACK-NEW"
    assert placements.created[0]["position_code"] == "SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_record_rack_arrived_reconciles_when_position_disabled() -> None:
    events = RecordingStateEventRepo()
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingRackPlacementRepo()
    service = ResourceProjectionService(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(enabled=False),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-002",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-disabled-001",
        idempotency_key="RACK_ARRIVED:wms-event-disabled-001:RACK-002",
        occurred_at=datetime(2026, 5, 18, 9, 1, 0),
        workline_id=1001,
        workline_session_id=2001,
        trace_id="trace-disabled",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "WORKLINE_RACK_POSITION_UNAVAILABLE"
    assert placements.created == []
    assert "validation_error" in events.created[0]["payload_json"]
    assert runtime_holds.created[0]["source_reason"] == "WORKLINE_RACK_POSITION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_record_rack_arrived_is_idempotent_for_same_rack_same_position() -> None:
    active = _rack_placement()
    placements = RecordingRackPlacementRepo(active_by_rack=active, active_by_position_list=[active])
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-001-repeat",
        idempotency_key="RACK_ARRIVED:wms-event-001-repeat:RACK-001",
        occurred_at=datetime(2026, 5, 18, 9, 2, 0),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert result.projection is active
    assert placements.created == []
    assert placements.updated == []


@pytest.mark.asyncio
async def test_record_rack_arrived_moves_same_rack_from_old_position() -> None:
    active = _rack_placement(
        id=7102,
        workline_code="SMT_SORTER_01",
        position_code="OLD_POSITION",
        location_code="OLD_POSITION",
    )
    placements = RecordingRackPlacementRepo(active_by_rack=active, active_by_position_list=[])
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        SimpleNamespace(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-move-001",
        idempotency_key="RACK_ARRIVED:wms-event-move-001:RACK-001",
        occurred_at=datetime(2026, 5, 18, 9, 3, 0),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert placements.updated[0][0] == 7102
    assert placements.updated[0][1]["ended_at"] == datetime(2026, 5, 18, 9, 3, 0)
    assert placements.created[0]["rack_code"] == "RACK-001"
    assert placements.created[0]["position_code"] == "SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_record_material_mounted_to_bin_cell_projects_active_mount() -> None:
    events = RecordingStateEventRepo()
    mounts = RecordingBinMaterialMountRepo()
    occupancies = RecordingBinCellOccupancyRepo()
    snapshots = RecordingResourceSnapshotService()
    service = ResourceProjectionService(
        state_event_repo=events,
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        snapshot_service=snapshots,
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
        workline_session_id=2001,
        reel_thickness="2.5",
        cell_capacity_depth_mm=10.0,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert mounts.material_identity_lookups == []
    assert occupancies.created[0]["bin_code"] == "BIN-001"
    assert occupancies.created[0]["reel_count"] == 1
    assert occupancies.created[0]["used_depth_mm"] == 2.5
    assert occupancies.created[0]["remaining_depth_mm"] == 7.5
    assert mounts.created[0]["bin_code"] == "BIN-001"
    assert mounts.created[0]["bin_cell_occupancy_id"] == 7701
    assert mounts.created[0]["cell_stack_position"] == 1
    assert mounts.created[0]["pkg_code"] == "PKG-001"
    assert mounts.created[0]["wms_inventory_id"] == "INV-001"
    assert mounts.created[0]["mount_status"] == BinMaterialMountStatus.OCCUPIED.value
    assert events.created[0]["event_type"] == "MATERIAL_MOUNTED"
    assert snapshots.material_calls == [
        {
            "bin_code": "BIN-001",
            "bin_cell_code": "BIN-001-4",
            "bin_cell_index": "4",
            "pkg_code": "PKG-001",
            "material_code": "620100L00-011-G",
            "lot_code": "8904936031",
            "date_code": "122625",
            "qty_snapshot": None,
            "wms_inventory_id": "INV-001",
            "reel_diameter": None,
            "reel_thickness": "2.5",
            "source_session_id": 2001,
            "source_event_id": "CMD-PICK-001",
            "captured_at": datetime(2026, 5, 18, 9, 5, 0),
        }
    ]


@pytest.mark.asyncio
async def test_record_material_mounted_to_bin_cell_preserves_decimal_depth_values() -> None:
    events = RecordingStateEventRepo()
    occupancies = RecordingBinCellOccupancyRepo()
    service = ResourceProjectionService(
        state_event_repo=events,
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=occupancies,
        snapshot_service=RecordingResourceSnapshotService(),
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
        source_event_id="CMD-PICK-DECIMAL",
        idempotency_key="MATERIAL_MOUNTED:CMD-PICK-DECIMAL:PKG-001:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-decimal",
        session_id="2001",
        workline_session_id=2001,
        reel_thickness="0.10",
        cell_capacity_depth_mm=Decimal("0.30"),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert occupancies.created[0]["used_depth_mm"] == Decimal("0.10")
    assert occupancies.created[0]["capacity_depth_mm"] == Decimal("0.30")
    assert occupancies.created[0]["remaining_depth_mm"] == Decimal("0.20")
    assert events.created[0]["payload_json"]["cell_capacity_depth_mm"] == "0.30"
    assert not isinstance(events.created[0]["payload_json"]["cell_capacity_depth_mm"], (Decimal, float))
    assert all(
        not isinstance(occupancies.created[0][key], float)
        for key in ("used_depth_mm", "capacity_depth_mm", "remaining_depth_mm")
    )


@pytest.mark.asyncio
async def test_record_bin_mounted_to_rack_records_empty_bin_snapshots() -> None:
    events = RecordingStateEventRepo()
    rack_bins = RecordingRackBinMountRepo(active_by_slot_map={}, active_by_bin_map={})
    snapshots = RecordingResourceSnapshotService()
    service = ResourceProjectionService(
        state_event_repo=events,
        rack_bin_mount_repo=rack_bins,
        snapshot_service=snapshots,
    )

    result = await service.record_bin_mounted_to_rack(
        SimpleNamespace(),
        rack_code="RACK-001",
        bin_mounts=[
            {"rack_slot_code": "A", "bin_code": "BIN-A"},
            {"rack_slot_code": "B", "bin_code": "BIN-B"},
            {"rack_slot_code": "C", "bin_code": "BIN-C"},
            {"rack_slot_code": "D", "bin_code": "BIN-D"},
        ],
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-MOUNTED-001",
        idempotency_key="BIN_MOUNTED:RACK-001:WMS-BIN-MOUNTED-001",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        session_id="2001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert [row["bin_code"] for row in rack_bins.created] == ["BIN-A", "BIN-B", "BIN-C", "BIN-D"]
    assert snapshots.empty_rack_calls == [
        {
            "rack_code": "RACK-001",
            "bin_mounts": [
                {"rack_slot_code": "A", "bin_code": "BIN-A"},
                {"rack_slot_code": "B", "bin_code": "BIN-B"},
                {"rack_slot_code": "C", "bin_code": "BIN-C"},
                {"rack_slot_code": "D", "bin_code": "BIN-D"},
            ],
            "source_session_id": 2001,
            "source_event_id": "WMS-BIN-MOUNTED-001",
            "captured_at": datetime(2026, 5, 18, 9, 5, 0),
        }
    ]


@pytest.mark.asyncio
async def test_record_material_mounted_to_occupied_bin_cell_creates_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    occupancies = RecordingBinCellOccupancyRepo(
        active_by_cell=_occupancy(
            material_identity_key="MAT:DIFFERENT:122624:DIFFERENT",
            material_code="DIFFERENT",
            lot_code="DIFFERENT",
            date_code="122624",
            source_event_id="old-event",
        )
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=occupancies,
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_CELL_MATERIAL_MOUNT_CONFLICT"
    assert occupancies.created == []
    assert runtime_holds.created[0]["source_reason"] == "BIN_CELL_MATERIAL_MOUNT_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_material_identity_key"] == "MAT:DIFFERENT:122624:DIFFERENT"


@pytest.mark.asyncio
async def test_record_material_mounted_to_same_identity_same_cell_projects_as_aggregate() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    snapshots = RecordingResourceSnapshotService()
    mounts = RecordingBinMaterialMountRepo()
    occupancies = RecordingBinCellOccupancyRepo(active_by_cell=_occupancy())
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        reel_thickness="2.5",
        cell_capacity_depth_mm=5.0,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert result.projection is None
    assert mounts.created[0]["pkg_code"] == "PKG-001"
    assert mounts.created[0]["bin_cell_occupancy_id"] == 7701
    assert mounts.created[0]["cell_stack_position"] == 2
    assert occupancies.saved[0]["reel_count"] == 2
    assert occupancies.saved[0]["used_depth_mm"] == 5.0
    assert occupancies.saved[0]["remaining_depth_mm"] == 0.0
    assert occupancies.saved[0]["occupancy_status"] == "FULL"
    assert runtime_holds.created == []
    assert snapshots.material_calls[0]["pkg_code"] == "PKG-001"


@pytest.mark.asyncio
async def test_record_material_mounted_to_legacy_identity_same_cell_projects_as_aggregate() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    snapshots = RecordingResourceSnapshotService()
    mounts = RecordingBinMaterialMountRepo()
    occupancies = RecordingBinCellOccupancyRepo(
        active_by_cell=_occupancy(material_identity_key="MAT:620100L00-011-G:122625:8904936031")
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
    )

    result = await service.record_material_mounted_to_bin_cell(
        SimpleNamespace(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:CC0402JRNPO9BN220:122625:8904936031",
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        reel_thickness="2.5",
        cell_capacity_depth_mm=5.0,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert runtime_holds.created == []
    assert occupancies.saved[0]["material_identity_key"] == "MAT:620100L00-011-G:CC0402JRNPO9BN220:122625:8904936031"
    assert occupancies.saved[0]["reel_count"] == 2
    assert mounts.created[0]["bin_cell_occupancy_id"] == 7701


@pytest.mark.asyncio
async def test_record_material_mounted_to_same_identity_same_cell_rejects_over_remaining_depth() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    snapshots = RecordingResourceSnapshotService()
    mounts = RecordingBinMaterialMountRepo()
    occupancies = RecordingBinCellOccupancyRepo(
        active_by_cell=_occupancy(
            reel_count=1,
            used_depth_mm=3.0,
            capacity_depth_mm=5.0,
            remaining_depth_mm=2.0,
            occupancy_status="OCCUPIED",
        )
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        reel_thickness="2.5",
        cell_capacity_depth_mm=5.0,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_CELL_CAPACITY_EXCEEDED"
    assert "重新分配" in (result.message or "")
    assert mounts.created == []
    assert occupancies.saved == []
    assert snapshots.material_calls == []
    assert runtime_holds.created[0]["source_reason"] == "BIN_CELL_CAPACITY_EXCEEDED"
    evidence = runtime_holds.created[0]["evidence"]
    assert evidence["active_bin_code"] == "BIN-001"
    assert evidence["active_bin_cell_index"] == "4"
    assert evidence["incoming_reel_thickness"] == "2.5"
    assert evidence["remaining_depth_mm"] == "2.0"
    assert evidence["capacity_depth_mm"] == "5.0"
    assert evidence["used_depth_mm"] == "3.0"
    assert evidence["requires_reallocation"] is True
    assert all(
        not isinstance(evidence[key], (Decimal, float))
        for key in ("incoming_reel_thickness", "remaining_depth_mm", "capacity_depth_mm", "used_depth_mm")
    )


@pytest.mark.asyncio
async def test_record_material_mounted_to_same_identity_other_nonfull_cell_creates_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    occupancies = RecordingBinCellOccupancyRepo(
        active_by_material_identity=[
            _occupancy(
                bin_code="BIN-OLD",
                bin_cell_code="BIN-OLD-5",
                bin_cell_index="5",
                remaining_depth_mm=2.5,
                occupancy_status="OCCUPIED",
            )
        ]
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=occupancies,
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_IDENTITY_MOUNT_CONFLICT"
    assert runtime_holds.created[0]["source_reason"] == "MATERIAL_IDENTITY_MOUNT_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_bin_code"] == "BIN-OLD"


@pytest.mark.asyncio
async def test_record_material_mounted_to_new_cell_when_existing_identity_cell_cannot_fit_reel() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    snapshots = RecordingResourceSnapshotService()
    mounts = RecordingBinMaterialMountRepo()
    occupancies = RecordingBinCellOccupancyRepo(
        active_by_material_identity=[
            _occupancy(
                bin_code="BIN-OLD",
                bin_cell_code="BIN-OLD-5",
                bin_cell_index="5",
                used_depth_mm=3.0,
                capacity_depth_mm=5.0,
                remaining_depth_mm=2.0,
                occupancy_status="OCCUPIED",
            )
        ]
    )
    service = ResourceProjectionService(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        reel_thickness="2.5",
        cell_capacity_depth_mm=5.0,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert runtime_holds.created == []
    assert occupancies.created[0]["bin_code"] == "BIN-001"
    assert occupancies.created[0]["bin_cell_index"] == "4"
    assert occupancies.created[0]["used_depth_mm"] == 2.5
    assert occupancies.created[0]["remaining_depth_mm"] == 2.5
    assert mounts.created[0]["bin_cell_occupancy_id"] == 7701
    assert snapshots.material_calls[0]["bin_code"] == "BIN-001"


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
        plugin_key="test_workline_plugin",
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
        plugin_key="test_workline_plugin",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "RACK_BIN_MOUNT_CONFLICT"
    assert rack_bins.created == []
    assert runtime_holds.created[0]["evidence"]["rack_slot_code"] == "B"
