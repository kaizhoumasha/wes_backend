from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.resource.models import (
    BinPlacement,
    BinPlacementStatus,
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


class RecordingBinPlacementRepo:
    def __init__(self, *, active_by_bin: BinPlacement | None = None, create_raises_integrity: bool = False) -> None:
        self.active_by_bin = active_by_bin
        self.create_raises_integrity = create_raises_integrity
        self.created: list[dict[str, Any]] = []
        self.closed_by_bin: list[dict[str, Any]] = []

    async def get_active_by_bin_code(self, _db: object, bin_code: str, **_kwargs: Any) -> BinPlacement | None:
        if self.active_by_bin is not None and self.active_by_bin.bin_code == bin_code:
            return self.active_by_bin
        return None

    async def get_active_by_placeholder_key(
        self,
        _db: object,
        _placeholder_key: str,
        **_kwargs: Any,
    ) -> BinPlacement | None:
        return None

    async def create(self, _db: object, data: dict[str, Any]) -> BinPlacement:
        if self.create_raises_integrity:
            raise IntegrityError("insert into resource_bin_placements", {}, Exception("duplicate active bin"))
        self.created.append(data)
        return BinPlacement(**data)

    async def close_active_by_bin_code(
        self,
        _db: object,
        bin_code: str,
        *,
        ended_at: datetime,
        source_event_id: str,
    ) -> int:
        self.closed_by_bin.append(
            {
                "bin_code": bin_code,
                "ended_at": ended_at,
                "source_event_id": source_event_id,
            }
        )
        return 1

    async def close_active_by_placeholder_key(
        self,
        _db: object,
        _placeholder_key: str,
        *,
        ended_at: datetime,
        source_event_id: str,
    ) -> int:
        _ = ended_at, source_event_id
        return 0


class RecordingRuntimeHoldCreator:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_for_resource_reconciliation(self, _db: object, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id=8801, **kwargs)


def _service(
    *,
    state_events: RecordingStateEventRepo | None = None,
    placements: RecordingBinPlacementRepo | None = None,
    runtime_holds: RecordingRuntimeHoldCreator | None = None,
) -> ResourceProjectionService:
    return ResourceProjectionService(
        state_event_repo=state_events or RecordingStateEventRepo(),
        bin_placement_repo=placements or RecordingBinPlacementRepo(),
        runtime_hold_creator=runtime_holds or RecordingRuntimeHoldCreator(),
    )


@pytest.mark.asyncio
async def test_bin_arrived_records_active_bin_placement() -> None:
    state_events = RecordingStateEventRepo()
    placements = RecordingBinPlacementRepo()
    service = _service(state_events=state_events, placements=placements)

    result = await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=301),
        workline=SimpleNamespace(id=45, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.BIN_ARRIVED.value,
        payload_json={
            "bin_code": "BIN-001",
            "position_type": "SORTER_STATION",
            "position_code": "SORTER-01",
            "source_system": ResourceSourceSystem.WMS.value,
            "source_event_id": "wms-bin-arrived-001",
            "occurred_at": datetime(2026, 5, 22, 8, 0, 0),
        },
        idempotency_key="BIN_ARRIVED:wms-bin-arrived-001:BIN-001",
        trace_id="trace-bin-001",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert state_events.created[0]["event_type"] == ResourceStateEventType.BIN_ARRIVED.value
    assert placements.created[0]["bin_code"] == "BIN-001"
    assert placements.created[0]["position_type"] == "SORTER_STATION"
    assert placements.created[0]["position_code"] == "SORTER-01"
    assert placements.created[0]["placement_status"] == BinPlacementStatus.ARRIVED.value


@pytest.mark.asyncio
async def test_bin_arrived_conflicts_when_known_bin_already_active() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingBinPlacementRepo(
        active_by_bin=BinPlacement(
            bin_code="BIN-001",
            position_type="BUFFER",
            position_code="OLD-BUFFER",
            placement_status=BinPlacementStatus.ARRIVED,
            source_system=ResourceSourceSystem.WES_RUNTIME,
            source_event_id="old-event",
            started_at=datetime(2026, 5, 22, 7, 0, 0),
        )
    )
    service = _service(placements=placements, runtime_holds=runtime_holds)

    result = await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=301),
        workline=SimpleNamespace(id=45, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.BIN_ARRIVED.value,
        payload_json={
            "bin_code": "BIN-001",
            "position_type": "SORTER_STATION",
            "position_code": "SORTER-01",
            "source_event_id": "wms-bin-arrived-002",
        },
        idempotency_key="BIN_ARRIVED:wms-bin-arrived-002:BIN-001",
        trace_id="trace-bin-001",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_ACTIVE_PLACEMENT_CONFLICT"
    assert result.runtime_hold is not None
    assert runtime_holds.created[0]["source_reason"] == "BIN_ACTIVE_PLACEMENT_CONFLICT"
    assert runtime_holds.created[0]["evidence"]["active_position_code"] == "OLD-BUFFER"
    assert placements.created == []


@pytest.mark.asyncio
async def test_bin_arrived_unique_constraint_race_enters_reconciling() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingBinPlacementRepo(create_raises_integrity=True)
    service = _service(placements=placements, runtime_holds=runtime_holds)

    result = await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=301),
        workline=SimpleNamespace(id=45, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.BIN_ARRIVED.value,
        payload_json={
            "bin_code": "BIN-001",
            "position_type": "SORTER_STATION",
            "position_code": "SORTER-01",
            "source_system": ResourceSourceSystem.WMS.value,
            "source_event_id": "wms-bin-arrived-race",
            "occurred_at": datetime(2026, 5, 22, 8, 0, 0),
        },
        idempotency_key="BIN_ARRIVED:wms-bin-arrived-race:BIN-001",
        trace_id="trace-bin-race",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_ACTIVE_PLACEMENT_CONCURRENT_CONFLICT"
    assert result.runtime_hold is not None
    assert runtime_holds.created[0]["source_reason"] == "BIN_ACTIVE_PLACEMENT_CONCURRENT_CONFLICT"


@pytest.mark.asyncio
async def test_bin_departed_closes_active_bin_placement() -> None:
    placements = RecordingBinPlacementRepo(
        active_by_bin=BinPlacement(
            bin_code="BIN-001",
            position_type="SORTER_STATION",
            position_code="SORTER-01",
            placement_status=BinPlacementStatus.ARRIVED,
            source_system=ResourceSourceSystem.WMS,
            source_event_id="wms-bin-arrived-001",
            started_at=datetime(2026, 5, 22, 8, 0, 0),
        )
    )
    service = _service(placements=placements)
    occurred_at = datetime(2026, 5, 22, 9, 0, 0)

    result = await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=301),
        workline=SimpleNamespace(id=45, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.BIN_DEPARTED.value,
        payload_json={
            "bin_code": "BIN-001",
            "source_event_id": "wms-bin-departed-001",
            "occurred_at": occurred_at,
        },
        idempotency_key="BIN_DEPARTED:wms-bin-departed-001:BIN-001",
        trace_id="trace-bin-001",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert placements.closed_by_bin == [
        {
            "bin_code": "BIN-001",
            "ended_at": occurred_at,
            "source_event_id": "wms-bin-departed-001",
        }
    ]


@pytest.mark.asyncio
async def test_bin_departed_without_active_placement_enters_reconciling() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingBinPlacementRepo()
    service = _service(placements=placements, runtime_holds=runtime_holds)

    result = await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=301),
        workline=SimpleNamespace(id=45, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.BIN_DEPARTED.value,
        payload_json={
            "bin_code": "BIN-404",
            "source_event_id": "wms-bin-departed-missing",
            "occurred_at": datetime(2026, 5, 22, 9, 0, 0),
        },
        idempotency_key="BIN_DEPARTED:wms-bin-departed-missing:BIN-404",
        trace_id="trace-bin-missing",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_ACTIVE_PLACEMENT_MISSING"
    assert result.runtime_hold is not None
    assert runtime_holds.created[0]["source_reason"] == "BIN_ACTIVE_PLACEMENT_MISSING"
    assert placements.closed_by_bin == []


@pytest.mark.asyncio
async def test_bin_departed_position_mismatch_enters_reconciling() -> None:
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingBinPlacementRepo(
        active_by_bin=BinPlacement(
            bin_code="BIN-001",
            position_type="SORTER_STATION",
            position_code="SORTER-01",
            placement_status=BinPlacementStatus.ARRIVED,
            source_system=ResourceSourceSystem.WMS,
            source_event_id="wms-bin-arrived-001",
            started_at=datetime(2026, 5, 22, 8, 0, 0),
        )
    )
    service = _service(placements=placements, runtime_holds=runtime_holds)

    result = await service.record_resource_fact(
        db=SimpleNamespace(),
        session=SimpleNamespace(id=301),
        workline=SimpleNamespace(id=45, line_code="SMT_SORTER_01"),
        fact_type=ResourceStateEventType.BIN_DEPARTED.value,
        payload_json={
            "bin_code": "BIN-001",
            "position_type": "BUFFER",
            "position_code": "BUFFER-01",
            "source_event_id": "wms-bin-departed-wrong-position",
            "occurred_at": datetime(2026, 5, 22, 9, 0, 0),
        },
        idempotency_key="BIN_DEPARTED:wms-bin-departed-wrong-position:BIN-001",
        trace_id="trace-bin-position-mismatch",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "BIN_PLACEMENT_POSITION_MISMATCH"
    assert result.runtime_hold is not None
    assert runtime_holds.created[0]["source_reason"] == "BIN_PLACEMENT_POSITION_MISMATCH"
    assert placements.closed_by_bin == []
