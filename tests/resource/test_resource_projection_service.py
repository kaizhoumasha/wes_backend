from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from src.app.resource.models import (
    BinMaterialMount,
    BinMaterialMountStatus,
    BinPlacement,
    BinPlacementStatus,
    RackBinMount,
    RackBinMountStatus,
    RackKind,
    RackPlacement,
    RackPlacementStatus,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
    ResourceType,
)
from src.app.resource.services import ResourceProjectionStatus
from src.app.resource.services.projection_service import ResourceProjectionService
from src.app.workline.models import ObjectTransitionDomain, ObjectTransitionEvent
from src.app.workline.models.material_unit import MaterialUnit, MaterialUnitStatus
from src.app.workline.services import ObjectTransitionEventService, object_transition_event_service
from src.database.sqlite_schema import configure_sqlite_schemas


@pytest_asyncio.fixture(scope="function")
async def transition_session():
    """独立内存 DB，用默认共享 transition service 验证真实落库链路。"""

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )
    configure_sqlite_schemas(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[cast("Any", ObjectTransitionEvent).__table__])
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=[cast("Any", ObjectTransitionEvent).__table__])
    await engine.dispose()


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


class RecordingObjectTransitionEventService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.keys: set[str] = set()

    @staticmethod
    def build_idempotency_key(
        *,
        source_event_id: str,
        domain: ObjectTransitionDomain | str,
        object_type: str,
        object_key: str,
        projection_type: str,
        to_state: str,
        reason_code: str,
    ) -> str:
        domain_value = domain.value if isinstance(domain, ObjectTransitionDomain) else str(domain)
        return (
            f"object-transition:{source_event_id}:{domain_value}:{object_type}:"
            f"{object_key}:{projection_type}:{to_state}:{reason_code}"
        )

    async def record_transition(self, _db: object, **kwargs: Any) -> SimpleNamespace:
        idempotency_key = kwargs.get("idempotency_key") or self.build_idempotency_key(
            source_event_id=kwargs["source_event_id"],
            domain=kwargs["domain"],
            object_type=kwargs["object_type"],
            object_key=kwargs["object_key"],
            projection_type=kwargs["projection_type"],
            to_state=kwargs["to_state"],
            reason_code=kwargs["reason_code"],
        )
        if idempotency_key not in self.keys:
            self.keys.add(idempotency_key)
            self.created.append({**kwargs, "idempotency_key": idempotency_key})
        return SimpleNamespace(id=len(self.created), **kwargs, idempotency_key=idempotency_key)


class NoopObjectTransitionEventService:
    async def record_transition(self, _db: object, **_kwargs: Any) -> None:
        return None


class AsyncSessionProxy:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session, name)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self.session.execute(*args, **kwargs)

    def get_bind(self, *args: Any, **kwargs: Any) -> Any:
        return self.session.get_bind(*args, **kwargs)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


def _db() -> AsyncSession:
    return cast("AsyncSession", SimpleNamespace())


def _session_proxy(session: AsyncSession) -> AsyncSession:
    return cast("AsyncSession", AsyncSessionProxy(session))


def _occurred_at(value: object) -> datetime:
    return cast("datetime", value)


def _projection_service(*, use_default_transition_service: bool = False, **kwargs: Any) -> ResourceProjectionService:
    if not use_default_transition_service:
        kwargs.setdefault("object_transition_event_service", NoopObjectTransitionEventService())
    kwargs.setdefault("material_unit_repository", NoopMaterialUnitRepository())
    return ResourceProjectionService(**kwargs)


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


class RecordingBinPlacementRepo:
    def __init__(
        self,
        *,
        active_by_bin: BinPlacement | None = None,
        active_by_placeholder: BinPlacement | None = None,
    ) -> None:
        self.active_by_bin = active_by_bin
        self.active_by_placeholder = active_by_placeholder
        self.created: list[dict[str, Any]] = []

    async def get_active_by_bin_code(
        self,
        _db: object,
        bin_code: str,
        *,
        for_update: bool = False,
    ) -> BinPlacement | None:
        assert for_update is True
        if self.active_by_bin is not None and self.active_by_bin.bin_code == bin_code:
            return self.active_by_bin
        return None

    async def get_active_by_placeholder_key(
        self,
        _db: object,
        placeholder_key: str,
        *,
        for_update: bool = False,
    ) -> BinPlacement | None:
        assert for_update is True
        if self.active_by_placeholder is not None and self.active_by_placeholder.placeholder_key == placeholder_key:
            return self.active_by_placeholder
        return None

    async def create(self, _db: object, data: dict[str, Any]) -> BinPlacement:
        self.created.append(data)
        return BinPlacement(**data)


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
        self.saved: list[dict[str, Any]] = []

    async def get_active_by_bin_cell(
        self, _db: object, *, bin_code: str, bin_cell_index: str
    ) -> BinMaterialMount | None:
        assert (bin_code, bin_cell_index) == ("BIN-001", "4")
        return self.active_by_cell

    async def list_active_by_bin_cell(
        self, _db: object, *, bin_code: str, bin_cell_index: str
    ) -> list[BinMaterialMount]:
        assert (bin_code, bin_cell_index) == ("BIN-001", "4")
        return [self.active_by_cell] if self.active_by_cell is not None else []

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

    async def save(self, _db: object, mount: BinMaterialMount) -> BinMaterialMount:
        self.saved.append(mount.model_dump())
        return mount


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


class NoopMaterialUnitRepository:
    async def update_current_location_by_pkg_code(
        self,
        _db: object,
        *,
        pkg_code: str,
        current_location: str | None,
    ) -> None:
        return None


class RecordingMaterialUnitRepository:
    def __init__(self, material_units: list[MaterialUnit]) -> None:
        self.material_units = {unit.pkg_code: unit for unit in material_units}
        self.location_updates: list[tuple[str, str | None]] = []

    async def update_current_location_by_pkg_code(
        self,
        _db: object,
        *,
        pkg_code: str,
        current_location: str | None,
    ) -> MaterialUnit | None:
        self.location_updates.append((pkg_code, current_location))
        material_unit = self.material_units.get(pkg_code)
        if material_unit is None:
            return None
        material_unit.current_location = current_location
        return material_unit


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


def _bin_placement(**overrides: Any) -> BinPlacement:
    values: dict[str, Any] = {
        "id": 7201,
        "bin_code": "BIN-001",
        "placeholder_key": None,
        "position_type": "BUFFER",
        "position_code": "BUFFER-01",
        "workline_id": 1001,
        "workline_code": "SMT_SORTER_01",
        "placement_status": BinPlacementStatus.ARRIVED,
        "source_system": ResourceSourceSystem.WMS,
        "source_event_id": "old-event",
        "started_at": datetime(2026, 5, 18, 8, 0, 0),
        "ended_at": None,
        "metadata_json": {},
    }
    values.update(overrides)
    return BinPlacement(**values)


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


def test_resource_transition_idempotency_keys_are_per_derived_sibling() -> None:
    service = RecordingObjectTransitionEventService()

    rack_key = service.build_idempotency_key(
        source_event_id="fact-001",
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="RACK",
        object_key="RACK-001",
        projection_type="RACK_PLACEMENT",
        to_state="ARRIVED",
        reason_code="RACK_ARRIVED",
    )
    bin_key = service.build_idempotency_key(
        source_event_id="fact-001",
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="BIN",
        object_key="BIN-001",
        projection_type="RACK_BIN_MOUNT",
        to_state="MOUNTED",
        reason_code="BIN_MOUNTED",
    )

    assert rack_key != bin_key
    assert "fact-001" in rack_key
    assert "fact-001" in bin_key


@pytest.mark.asyncio
async def test_record_rack_arrived_at_workline_position_projects_active_placement() -> None:
    events = RecordingStateEventRepo()
    placements = RecordingRackPlacementRepo()
    positions = RecordingRackPositionService()
    service = _projection_service(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=positions,
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-001",
        idempotency_key="RACK_ARRIVED:wms-event-001:RACK-001",
        occurred_at=datetime(2026, 5, 18, 9, 0, 0),
        trace_id="trace-001",
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
async def test_record_rack_arrived_at_workline_position_records_transition_event() -> None:
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=RecordingRackPlacementRepo(),
        rack_position_service=RecordingRackPositionService(),
        object_transition_event_service=transitions,
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-001",
        idempotency_key="RACK_ARRIVED:wms-event-001:RACK-001",
        occurred_at=datetime(2026, 5, 18, 9, 0, 0),
        trace_id="trace-001",
        workline_session_id=2001,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert transitions.created == [
        {
            "domain": ObjectTransitionDomain.RESOURCE,
            "object_type": "RACK",
            "object_key": "RACK-001",
            "projection_type": "RACK_PLACEMENT",
            "from_state": None,
            "to_state": "ARRIVED",
            "reason_code": "RACK_ARRIVED",
            "source_event_id": "wms-event-001",
            "source_ref_json": {
                "resource_state_event_type": "RACK_ARRIVED",
                "rack_code": "RACK-001",
            },
            "evidence_json": {
                "workline_code": "SMT_SORTER_01",
                "position_code": "SINGLE_LAYER_A",
                "rack_kind": "SINGLE_LAYER",
            },
            "workline_session_id": 2001,
            "trace_id": "trace-001",
            "occurred_at": datetime(2026, 5, 18, 9, 0, 0),
            "auto_commit": False,
            "idempotency_key": "object-transition:wms-event-001:RESOURCE:RACK:RACK-001:RACK_PLACEMENT:ARRIVED:RACK_ARRIVED",
        }
    ]


@pytest.mark.asyncio
async def test_record_rack_arrived_accepts_millisecond_timestamp() -> None:
    events = RecordingStateEventRepo()
    placements = RecordingRackPlacementRepo()
    service = _projection_service(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-ms-001",
        idempotency_key="RACK_ARRIVED:wms-event-ms-001:RACK-001",
        occurred_at=_occurred_at(1780457720161),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert events.created[0]["occurred_at"] == datetime(2026, 6, 3, 3, 35, 20, 161000)
    assert placements.created[0]["started_at"] == datetime(2026, 6, 3, 3, 35, 20, 161000)


@pytest.mark.asyncio
async def test_record_rack_arrived_falls_back_for_invalid_numeric_timestamp(monkeypatch) -> None:
    fallback_now = datetime(2026, 6, 3, 4, 0, 0)
    monkeypatch.setattr(
        "src.app.resource.services.projection_service.timezone.now_for_db",
        lambda: fallback_now,
    )
    events = RecordingStateEventRepo()
    placements = RecordingRackPlacementRepo()
    service = _projection_service(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-invalid-time-001",
        idempotency_key="RACK_ARRIVED:wms-event-invalid-time-001:RACK-001",
        occurred_at=_occurred_at(float("inf")),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert events.created[0]["occurred_at"] == fallback_now
    assert placements.created[0]["started_at"] == fallback_now


@pytest.mark.asyncio
async def test_record_rack_arrived_falls_back_for_huge_integer_timestamp(monkeypatch) -> None:
    fallback_now = datetime(2026, 6, 3, 4, 5, 0)
    monkeypatch.setattr(
        "src.app.resource.services.projection_service.timezone.now_for_db",
        lambda: fallback_now,
    )
    events = RecordingStateEventRepo()
    placements = RecordingRackPlacementRepo()
    service = _projection_service(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-001",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-huge-time-001",
        idempotency_key="RACK_ARRIVED:wms-event-huge-time-001:RACK-001",
        occurred_at=_occurred_at(10**1000),
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert events.created[0]["occurred_at"] == fallback_now
    assert placements.created[0]["started_at"] == fallback_now


@pytest.mark.asyncio
async def test_record_rack_arrived_allows_second_rack_when_position_capacity_two() -> None:
    placements = RecordingRackPlacementRepo(active_by_position_list=[_rack_placement()])
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=2),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
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
async def test_record_rack_arrived_reconciling_capacity_records_transition_evidence() -> None:
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=RecordingRackPlacementRepo(active_by_position_list=[_rack_placement()]),
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
        object_transition_event_service=transitions,
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-002",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-capacity",
        idempotency_key="RACK_ARRIVED:wms-event-capacity:RACK-002",
        occurred_at=datetime(2026, 5, 18, 9, 1, 0),
        workline_id=1001,
        workline_session_id=2001,
        trace_id="trace-capacity",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    transition = transitions.created[0]
    assert transition["object_type"] == "RACK"
    assert transition["object_key"] == "RACK-002"
    assert transition["projection_type"] == "RACK_PLACEMENT"
    assert transition["to_state"] == "RECONCILING"
    assert transition["reason_code"] == "WORKLINE_POSITION_CAPACITY_EXHAUSTED"
    assert transition["evidence_json"]["trusted"] is False
    assert transition["evidence_json"]["capacity"] == 1
    assert transition["evidence_json"]["active_count"] == 1


@pytest.mark.asyncio
async def test_record_rack_arrived_releases_declared_old_rack_before_capacity_check() -> None:
    transitions = RecordingObjectTransitionEventService()
    old_placement = _rack_placement(
        id=7101,
        rack_code="RACK-OLD",
        placement_status=RackPlacementStatus.ARRIVED,
    )
    placements = RecordingRackPlacementRepo(active_by_position_list=[old_placement])
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
        object_transition_event_service=transitions,
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
        rack_code="RACK-NEW",
        rack_kind=RackKind.SINGLE_LAYER,
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-replace-001",
        idempotency_key="RACK_ARRIVED:wms-event-replace-001:RACK-NEW",
        occurred_at=datetime(2026, 5, 18, 9, 1, 0),
        released_rack_codes=["RACK-OLD"],
        workline_session_id=2001,
        trace_id="trace-release-old-rack",
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
    assert [transition["object_key"] for transition in transitions.created] == ["RACK-OLD", "RACK-NEW"]
    release_transition = transitions.created[0]
    assert release_transition["domain"] == ObjectTransitionDomain.RESOURCE
    assert release_transition["object_type"] == "RACK"
    assert release_transition["projection_type"] == "RACK_PLACEMENT"
    assert release_transition["from_state"] == RackPlacementStatus.ARRIVED.value
    assert release_transition["to_state"] == RackPlacementStatus.DEPARTED.value
    assert release_transition["reason_code"] == "RACK_RELEASED_BY_NEW_ARRIVAL"
    assert release_transition["source_event_id"] == "wms-event-replace-001"
    assert release_transition["source_ref_json"] == {
        "resource_state_event_type": ResourceStateEventType.RACK_ARRIVED.value,
        "rack_code": "RACK-NEW",
        "released_rack_code": "RACK-OLD",
    }
    assert release_transition["evidence_json"]["workline_code"] == "SMT_SORTER_01"
    assert release_transition["evidence_json"]["position_code"] == "SINGLE_LAYER_A"
    assert release_transition["workline_session_id"] == 2001
    assert release_transition["trace_id"] == "trace-release-old-rack"


@pytest.mark.asyncio
async def test_record_rack_arrived_reconciles_when_position_disabled() -> None:
    events = RecordingStateEventRepo()
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingRackPlacementRepo()
    service = _projection_service(
        state_event_repo=events,
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(enabled=False),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_placement_repo=placements,
        rack_position_service=RecordingRackPositionService(capacity=1),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
    )

    result = await service.record_rack_arrived_at_workline_position(
        _db(),
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
    service = _projection_service(
        state_event_repo=events,
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        snapshot_service=snapshots,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
        workline_session_id=2001,
        workline_id=1001,
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
    assert events.created[0]["workline_id"] == 1001
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
async def test_record_material_mounted_to_bin_cell_records_transition_event() -> None:
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        snapshot_service=RecordingResourceSnapshotService(),
        object_transition_event_service=transitions,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
        workline_session_id=2001,
        reel_thickness="2.5",
        cell_capacity_depth_mm=10.0,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    transition = transitions.created[0]
    assert transition["domain"] == ObjectTransitionDomain.RESOURCE
    assert transition["object_type"] == "MATERIAL"
    assert transition["object_key"] == "PKG-001"
    assert transition["projection_type"] == "BIN_CELL_MOUNT"
    assert transition["to_state"] == "MOUNTED"
    assert transition["reason_code"] == "MATERIAL_MOUNTED"
    assert transition["source_event_id"] == "CMD-PICK-001"
    assert transition["workline_session_id"] == 2001
    assert transition["trace_id"] == "trace-001"
    assert transition["source_ref_json"]["bin_code"] == "BIN-001"
    assert transition["source_ref_json"]["bin_cell_index"] == "4"
    assert transition["evidence_json"]["material_identity_key"] == "MAT:620100L00-011-G:122625:8904936031"


@pytest.mark.asyncio
async def test_record_material_mounted_to_bin_cell_updates_material_unit_location_cache_without_status() -> None:
    material_unit = MaterialUnit(
        pkg_code="PKG-001",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        six_in_one={},
        status=MaterialUnitStatus.NG,
        current_location=None,
    )
    material_units = RecordingMaterialUnitRepository([material_unit])
    db = _db()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(),
        snapshot_service=RecordingResourceSnapshotService(),
        material_unit_repository=material_units,
    )

    result = await service.record_material_mounted_to_bin_cell(
        db,
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
        reel_thickness="2.5",
        cell_capacity_depth_mm=10.0,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert material_unit.current_location == "BIN-001:4"
    assert material_unit.status == MaterialUnitStatus.NG
    assert material_units.location_updates == [("PKG-001", "BIN-001:4")]


@pytest.mark.asyncio
async def test_record_material_mounted_to_bin_cell_preserves_decimal_depth_values() -> None:
    events = RecordingStateEventRepo()
    occupancies = RecordingBinCellOccupancyRepo()
    service = _projection_service(
        state_event_repo=events,
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=occupancies,
        snapshot_service=RecordingResourceSnapshotService(),
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
async def test_record_material_unmounted_from_bin_cell_closes_top_mount_and_updates_occupancy() -> None:
    mounts = RecordingBinMaterialMountRepo(
        active_by_cell=_mount(
            cell_stack_position=2,
            pkg_code="PKG-TOP",
            wms_inventory_id="INV-TOP",
            reel_thickness="0.10",
            source_version="7",
        )
    )
    occupancy = _occupancy(
        reel_count=2,
        used_depth_mm=Decimal("0.20"),
        capacity_depth_mm=Decimal("0.30"),
        remaining_depth_mm=Decimal("0.10"),
    )
    events = RecordingStateEventRepo()
    occupancies = RecordingBinCellOccupancyRepo(active_by_cell=occupancy)
    service = _projection_service(
        state_event_repo=events,
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        source_version="8",
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
        reel_thickness="0.10",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert mounts.saved[0]["mount_status"] == BinMaterialMountStatus.REMOVED
    assert mounts.saved[0]["ended_at"] == datetime(2026, 5, 18, 9, 10, 0)
    assert occupancies.saved[0]["reel_count"] == 1
    assert occupancies.saved[0]["used_depth_mm"] == Decimal("0.10")
    assert occupancies.saved[0]["remaining_depth_mm"] == Decimal("0.20")
    assert occupancies.saved[0]["occupancy_status"] == "OCCUPIED"
    assert occupancies.saved[0]["ended_at"] is None
    assert events.created[0]["workline_id"] == 1001


@pytest.mark.asyncio
async def test_record_material_unmounted_from_bin_cell_records_transition_event() -> None:
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(
            active_by_cell=_mount(
                cell_stack_position=1,
                pkg_code="PKG-TOP",
                wms_inventory_id="INV-TOP",
                reel_thickness="0.10",
            )
        ),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(
            active_by_cell=_occupancy(
                reel_count=1,
                used_depth_mm=Decimal("0.10"),
                capacity_depth_mm=Decimal("0.30"),
                remaining_depth_mm=Decimal("0.20"),
            )
        ),
        object_transition_event_service=transitions,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        reel_thickness="0.10",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    transition = transitions.created[0]
    assert transition["object_type"] == "MATERIAL"
    assert transition["object_key"] == "PKG-TOP"
    assert transition["projection_type"] == "BIN_CELL_MOUNT"
    assert transition["from_state"] == "MOUNTED"
    assert transition["to_state"] == "UNMOUNTED"
    assert transition["reason_code"] == "MATERIAL_UNMOUNTED"
    assert transition["source_ref_json"]["bin_code"] == "BIN-001"
    assert transition["source_ref_json"]["bin_cell_index"] == "4"
    assert transition["evidence_json"]["material_identity_key"] == "MAT:620100L00-011-G:122625:8904936031"


@pytest.mark.asyncio
async def test_record_material_unmounted_from_bin_cell_clears_location_cache_without_status() -> None:
    material_unit = MaterialUnit(
        pkg_code="PKG-TOP",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        six_in_one={},
        status=MaterialUnitStatus.RECONCILING,
        current_location="BIN-001:4",
    )
    material_units = RecordingMaterialUnitRepository([material_unit])
    db = _db()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(
            active_by_cell=_mount(
                cell_stack_position=1,
                pkg_code="PKG-TOP",
                wms_inventory_id="INV-TOP",
                reel_thickness="0.10",
                source_version="7",
            )
        ),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(
            active_by_cell=_occupancy(
                reel_count=1,
                used_depth_mm=Decimal("0.10"),
                capacity_depth_mm=Decimal("0.30"),
                remaining_depth_mm=Decimal("0.20"),
            )
        ),
        material_unit_repository=material_units,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        db,
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        source_version="8",
        reel_thickness="0.10",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert material_unit.current_location is None
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_units.location_updates == [("PKG-TOP", None)]


@pytest.mark.asyncio
async def test_record_material_unmounted_duplicate_event_does_not_double_decrement() -> None:
    existing_event = ResourceStateEvent(
        event_code="WES_RUNTIME:CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        event_type=ResourceStateEventType.MATERIAL_UNMOUNTED,
        resource_type=ResourceType.MATERIAL,
        resource_code="PKG-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        payload_json={},
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        received_at=datetime(2026, 5, 18, 9, 10, 1),
    )
    mounts = RecordingBinMaterialMountRepo(active_by_cell=_mount(pkg_code="PKG-TOP"))
    occupancies = RecordingBinCellOccupancyRepo(active_by_cell=_occupancy(reel_count=1))
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(existing_event),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id=None,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
    )

    assert result.status == ResourceProjectionStatus.DUPLICATE
    assert mounts.saved == []
    assert occupancies.saved == []


@pytest.mark.asyncio
async def test_duplicate_resource_state_event_does_not_record_transition_event() -> None:
    existing_event = ResourceStateEvent(
        event_code="WES_RUNTIME:CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        event_type=ResourceStateEventType.MATERIAL_UNMOUNTED,
        resource_type=ResourceType.MATERIAL,
        resource_code="PKG-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        payload_json={},
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        received_at=datetime(2026, 5, 18, 9, 10, 1),
    )
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(existing_event),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(active_by_cell=_mount(pkg_code="PKG-TOP")),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy(reel_count=1)),
        object_transition_event_service=transitions,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id=None,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-001",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-001:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
    )

    assert result.status == ResourceProjectionStatus.DUPLICATE
    assert transitions.created == []


@pytest.mark.asyncio
async def test_record_material_unmounted_missing_top_mount_creates_reconciliation_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(active_by_cell=None),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy()),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-MISSING",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-MISSING:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_UNMOUNTED_ACTIVE_MOUNT_MISSING"
    assert runtime_holds.created[0]["source_reason"] == "MATERIAL_UNMOUNTED_ACTIVE_MOUNT_MISSING"
    assert runtime_holds.created[0]["evidence"]["source_session_id"] == 2001
    assert runtime_holds.created[0]["evidence"]["source_event_id"] == "CMD-UNMOUNT-MISSING"
    assert runtime_holds.created[0]["evidence"]["bin_code"] == "BIN-001"
    assert runtime_holds.created[0]["evidence"]["bin_cell_index"] == "4"


@pytest.mark.asyncio
async def test_record_material_unmounted_reconciling_records_transition_evidence() -> None:
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(active_by_cell=None),
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy()),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
        object_transition_event_service=transitions,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-MISSING",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-MISSING:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    transition = transitions.created[0]
    assert transition["to_state"] == "RECONCILING"
    assert transition["reason_code"] == "MATERIAL_UNMOUNTED_ACTIVE_MOUNT_MISSING"
    assert transition["evidence_json"]["trusted"] is False
    assert transition["evidence_json"]["bin_code"] == "BIN-001"
    assert transition["evidence_json"]["bin_cell_index"] == "4"


@pytest.mark.asyncio
async def test_record_material_unmounted_identity_mismatch_creates_reconciliation_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(
        active_by_cell=_mount(
            material_identity_key="MAT:DIFFERENT:122625:8904936031",
            pkg_code="PKG-DIFFERENT",
            wms_inventory_id="INV-DIFFERENT",
        )
    )
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy()),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-MISMATCH",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-MISMATCH:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_UNMOUNTED_IDENTITY_MISMATCH"
    evidence = runtime_holds.created[0]["evidence"]
    assert evidence["expected_material_identity_key"] == "MAT:620100L00-011-G:122625:8904936031"
    assert evidence["active_material_identity_key"] == "MAT:DIFFERENT:122625:8904936031"
    assert evidence["active_pkg_code"] == "PKG-DIFFERENT"


@pytest.mark.asyncio
async def test_record_material_unmounted_missing_active_pkg_when_expected_creates_reconciliation_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(active_by_cell=_mount(pkg_code=None, wms_inventory_id=None))
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy()),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id="INV-TOP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-MISSING-PKG",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-MISSING-PKG:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_UNMOUNTED_IDENTITY_MISMATCH"
    assert mounts.saved == []
    assert runtime_holds.created[0]["evidence"]["expected_pkg_code"] == "PKG-TOP"
    assert runtime_holds.created[0]["evidence"]["active_pkg_code"] is None


@pytest.mark.asyncio
async def test_record_material_unmounted_source_version_mismatch_creates_reconciliation_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(active_by_cell=_mount(pkg_code="PKG-TOP", source_version="9"))
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy()),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id=None,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-STALE",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-STALE:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        source_version="8",
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_UNMOUNTED_SOURCE_VERSION_STALE"
    evidence = runtime_holds.created[0]["evidence"]
    assert evidence["source_version"] == "8"
    assert evidence["active_source_version"] == "9"


@pytest.mark.asyncio
async def test_record_material_unmounted_inconsistent_occupancy_creates_reconciliation_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(active_by_cell=_mount(pkg_code="PKG-TOP", bin_cell_occupancy_id=9901))
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(active_by_cell=_occupancy(id=7701)),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id=None,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-INCONSISTENT",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-INCONSISTENT:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_UNMOUNTED_OCCUPANCY_INCONSISTENT"
    evidence = runtime_holds.created[0]["evidence"]
    assert evidence["active_occupancy_id"] == 7701
    assert evidence["mount_occupancy_id"] == 9901


@pytest.mark.asyncio
async def test_record_material_unmounted_used_depth_less_than_outgoing_depth_reconciles() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    mounts = RecordingBinMaterialMountRepo(
        active_by_cell=_mount(pkg_code="PKG-TOP", reel_thickness="0.10", bin_cell_occupancy_id=7701)
    )
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=RecordingBinCellOccupancyRepo(
            active_by_cell=_occupancy(
                id=7701,
                reel_count=1,
                used_depth_mm=Decimal("0.05"),
                capacity_depth_mm=Decimal("0.30"),
                remaining_depth_mm=Decimal("0.25"),
            )
        ),
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_unmounted_from_bin_cell(
        _db(),
        bin_code="BIN-001",
        bin_cell_code="BIN-001-4",
        bin_cell_index="4",
        material_identity_key="MAT:620100L00-011-G:122625:8904936031",
        pkg_code="PKG-TOP",
        wms_inventory_id=None,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="CMD-UNMOUNT-DEPTH",
        idempotency_key="MATERIAL_UNMOUNTED:CMD-UNMOUNT-DEPTH:PKG-TOP:BIN-001:4",
        occurred_at=datetime(2026, 5, 18, 9, 10, 0),
        trace_id="trace-unmount",
        workline_session_id=2001,
        workline_id=1001,
        reel_thickness="0.10",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "MATERIAL_UNMOUNTED_OCCUPANCY_INCONSISTENT"
    assert mounts.saved == []
    evidence = runtime_holds.created[0]["evidence"]
    assert evidence["active_used_depth_mm"] == "0.05"
    assert evidence["outgoing_reel_thickness"] == "0.10"


@pytest.mark.asyncio
async def test_record_bin_mounted_to_rack_records_empty_bin_snapshots() -> None:
    events = RecordingStateEventRepo()
    rack_bins = RecordingRackBinMountRepo(active_by_slot_map={}, active_by_bin_map={})
    snapshots = RecordingResourceSnapshotService()
    service = _projection_service(
        state_event_repo=events,
        rack_bin_mount_repo=rack_bins,
        snapshot_service=snapshots,
    )

    result = await service.record_bin_mounted_to_rack(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=RecordingBinMaterialMountRepo(),
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_material_mount_repo=mounts,
        bin_cell_occupancy_repo=occupancies,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
    )

    result = await service.record_material_mounted_to_bin_cell(
        _db(),
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
async def test_record_bin_arrived_at_position_records_transition_event() -> None:
    transitions = RecordingObjectTransitionEventService()
    placements = RecordingBinPlacementRepo()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_placement_repo=placements,
        object_transition_event_service=transitions,
    )

    result = await service.record_bin_arrived_at_position(
        _db(),
        bin_code="BIN-001",
        position_type="BUFFER",
        position_code="BUFFER-01",
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-ARRIVED-001",
        idempotency_key="BIN_ARRIVED:WMS-BIN-ARRIVED-001:BIN-001",
        occurred_at=datetime(2026, 5, 18, 9, 4, 0),
        trace_id="trace-bin",
        workline_session_id=2001,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert placements.created[0]["bin_code"] == "BIN-001"
    transition = transitions.created[0]
    assert transition["domain"] == ObjectTransitionDomain.RESOURCE
    assert transition["object_type"] == "BIN"
    assert transition["object_key"] == "BIN-001"
    assert transition["projection_type"] == "BIN_PLACEMENT"
    assert transition["to_state"] == "ARRIVED"
    assert transition["reason_code"] == "BIN_ARRIVED"
    assert transition["source_event_id"] == "WMS-BIN-ARRIVED-001"
    assert transition["workline_session_id"] == 2001
    assert transition["trace_id"] == "trace-bin"
    assert transition["source_ref_json"]["resource_state_event_type"] == "BIN_ARRIVED"
    assert transition["evidence_json"]["position_type"] == "BUFFER"
    assert transition["evidence_json"]["position_code"] == "BUFFER-01"


@pytest.mark.asyncio
async def test_record_bin_arrived_with_default_transition_service_persists_event(
    transition_session: AsyncSession,
) -> None:
    placements = RecordingBinPlacementRepo()
    service = _projection_service(
        use_default_transition_service=True,
        state_event_repo=RecordingStateEventRepo(),
        bin_placement_repo=placements,
    )

    result = await service.record_bin_arrived_at_position(
        transition_session,
        bin_code="BIN-001",
        position_type="BUFFER",
        position_code="BUFFER-01",
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-ARRIVED-DB-001",
        idempotency_key="BIN_ARRIVED:WMS-BIN-ARRIVED-DB-001:BIN-001",
        occurred_at=datetime(2026, 5, 18, 9, 4, 0),
        trace_id="trace-bin-db",
        workline_session_id=2001,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    by_source = await object_transition_event_service.get_by_source_event(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        source_event_id="WMS-BIN-ARRIVED-DB-001",
    )
    by_object = await object_transition_event_service.get_by_object(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="BIN",
        object_key="BIN-001",
    )
    assert len(by_source) == 1
    assert by_source[0].id == by_object[0].id
    assert by_source[0].projection_type == "BIN_PLACEMENT"
    assert by_source[0].to_state == "ARRIVED"
    assert by_source[0].reason_code == "BIN_ARRIVED"
    assert by_source[0].workline_session_id == 2001
    assert by_source[0].trace_id == "trace-bin-db"


@pytest.mark.asyncio
async def test_default_transition_service_does_not_skip_async_session_proxy(
    transition_session: AsyncSession,
) -> None:
    placements = RecordingBinPlacementRepo()
    service = _projection_service(
        use_default_transition_service=True,
        state_event_repo=RecordingStateEventRepo(),
        bin_placement_repo=placements,
    )

    result = await service.record_bin_arrived_at_position(
        _session_proxy(transition_session),
        bin_code="BIN-PROXY",
        position_type="BUFFER",
        position_code="BUFFER-01",
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-ARRIVED-PROXY",
        idempotency_key="BIN_ARRIVED:WMS-BIN-ARRIVED-PROXY:BIN-PROXY",
        occurred_at=datetime(2026, 5, 18, 9, 4, 0),
        trace_id="trace-bin-proxy",
        workline_session_id=2001,
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    transitions = await object_transition_event_service.get_by_source_event(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        source_event_id="WMS-BIN-ARRIVED-PROXY",
    )
    assert len(transitions) == 1
    assert transitions[0].object_key == "BIN-PROXY"


@pytest.mark.asyncio
async def test_record_bin_arrived_reconciling_records_transition_evidence() -> None:
    transitions = RecordingObjectTransitionEventService()
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        bin_placement_repo=RecordingBinPlacementRepo(active_by_bin=_bin_placement()),
        runtime_hold_creator=RecordingRuntimeHoldCreator(),
        object_transition_event_service=transitions,
    )

    result = await service.record_bin_arrived_at_position(
        _db(),
        bin_code="BIN-001",
        position_type="BUFFER",
        position_code="BUFFER-02",
        workline_id=1001,
        workline_code="SMT_SORTER_01",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-ARRIVED-CONFLICT",
        idempotency_key="BIN_ARRIVED:WMS-BIN-ARRIVED-CONFLICT:BIN-001",
        occurred_at=datetime(2026, 5, 18, 9, 4, 0),
        trace_id="trace-bin-conflict",
        workline_session_id=2001,
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    transition = transitions.created[0]
    assert transition["object_type"] == "BIN"
    assert transition["object_key"] == "BIN-001"
    assert transition["projection_type"] == "BIN_PLACEMENT"
    assert transition["to_state"] == "RECONCILING"
    assert transition["reason_code"] == "BIN_ACTIVE_PLACEMENT_CONFLICT"
    assert transition["evidence_json"]["trusted"] is False
    assert transition["evidence_json"]["active_position_code"] == "BUFFER-01"
    assert transition["evidence_json"]["incoming_position_code"] == "BUFFER-02"


@pytest.mark.asyncio
async def test_record_bin_mounted_to_rack_conflict_creates_hold() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    rack_bins = RecordingRackBinMountRepo(active_by_slot=_rack_bin_mount())
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_bin_mount_repo=rack_bins,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_bin_mounted_to_rack(
        _db(),
        rack_code="RACK-001",
        bin_mounts=[{"rack_slot_code": "A", "bin_code": "BIN-001"}],
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-MOUNTED-001",
        idempotency_key="BIN_MOUNTED:RACK-001:WMS-BIN-MOUNTED-001",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
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
async def test_record_bin_mounted_to_rack_same_active_mount_is_idempotent() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    snapshots = RecordingResourceSnapshotService()
    active_mount = _rack_bin_mount(rack_code="RACK-001", rack_slot_code="A", bin_code="BIN-001")
    rack_bins = RecordingRackBinMountRepo(active_by_slot=active_mount, active_by_bin=active_mount)
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_bin_mount_repo=rack_bins,
        runtime_hold_creator=runtime_holds,
        snapshot_service=snapshots,
    )

    result = await service.record_bin_mounted_to_rack(
        _db(),
        rack_code="RACK-001",
        bin_mounts=[{"rack_slot_code": "A", "bin_code": "BIN-001"}],
        source_system=ResourceSourceSystem.WMS,
        source_event_id="WMS-BIN-MOUNTED-001",
        idempotency_key="BIN_MOUNTED:RACK-001:WMS-BIN-MOUNTED-001",
        occurred_at=datetime(2026, 5, 18, 9, 5, 0),
        trace_id="trace-001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert rack_bins.created == []
    assert runtime_holds.created == []
    assert snapshots.empty_rack_calls[0]["bin_mounts"] == [{"rack_slot_code": "A", "bin_code": "BIN-001"}]


@pytest.mark.asyncio
async def test_record_bin_mounted_to_rack_conflict_on_later_mount_does_not_create_partial_projection() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    rack_bins = RecordingRackBinMountRepo(
        active_by_slot_map={
            ("RACK-001", "B"): _rack_bin_mount(rack_slot_code="B", bin_code="BIN-OLD"),
        }
    )
    service = _projection_service(
        state_event_repo=RecordingStateEventRepo(),
        rack_bin_mount_repo=rack_bins,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_bin_mounted_to_rack(
        _db(),
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
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "RACK_BIN_MOUNT_CONFLICT"
    assert rack_bins.created == []
    assert runtime_holds.created[0]["evidence"]["rack_slot_code"] == "B"
