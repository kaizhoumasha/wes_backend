"""资源关系服务测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import RackPlacement, ResourceSourceSystem, ResourceStateEvent


class RecordingStateEventRepo:
    def __init__(self, existing_event: ResourceStateEvent | None = None) -> None:
        self.existing_event = existing_event
        self.created: list[dict[str, Any]] = []

    async def get_by_source_event_id(
        self,
        _db: object,
        *,
        source_system: ResourceSourceSystem,
        source_event_id: str,
    ) -> ResourceStateEvent | None:
        assert source_system == ResourceSourceSystem.WMS
        assert source_event_id
        return self.existing_event

    async def create(self, _db: object, data: dict[str, Any]) -> ResourceStateEvent:
        self.created.append(data)
        return ResourceStateEvent(**data)


class RecordingPlacementRepo:
    def __init__(self, active_placement: RackPlacement | None = None) -> None:
        self.active_placement = active_placement
        self.created: list[dict[str, Any]] = []

    async def get_active_by_rack_code(self, _db: object, rack_code: str) -> RackPlacement | None:
        assert rack_code == "RACK-001"
        return self.active_placement

    async def create(self, _db: object, data: dict[str, Any]) -> RackPlacement:
        self.created.append(data)
        return RackPlacement(**data)


class RecordingRuntimeHoldCreator:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_for_resource_reconciliation(self, _db: object, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id=9001, **kwargs)


@pytest.mark.asyncio
async def test_record_rack_arrived_appends_fact_and_creates_active_placement() -> None:
    """货架到达事件先写 ResourceStateEvent，再创建 active RackPlacement 投影。"""

    from src.app.resource.services import ResourceProjectionStatus, ResourceRelationService

    state_events = RecordingStateEventRepo()
    placements = RecordingPlacementRepo()
    service = ResourceRelationService(state_event_repo=state_events, rack_placement_repo=placements)

    result = await service.record_rack_arrived(
        object(),
        rack_code="RACK-001",
        location_code="LOC-001",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-001",
        source_version="1",
        source_task_id="wms-task-001",
        occurred_at=datetime(2026, 5, 16, 8, 0, 0),
        trace_id="trace-001",
        session_id="session-001",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert result.reason_code is None
    assert state_events.created[0]["event_type"] == "RACK_ARRIVED"
    assert state_events.created[0]["resource_code"] == "RACK-001"
    assert placements.created[0]["rack_code"] == "RACK-001"
    assert placements.created[0]["location_code"] == "LOC-001"
    assert placements.created[0]["ended_at"] is None


@pytest.mark.asyncio
async def test_record_rack_arrived_conflict_does_not_overwrite_active_placement() -> None:
    """同一货架已有其他 active placement 时，追加事实、创建 RuntimeHold 且不覆盖投影。"""

    from src.app.resource.services import ResourceProjectionStatus, ResourceRelationService

    state_events = RecordingStateEventRepo()
    runtime_holds = RecordingRuntimeHoldCreator()
    placements = RecordingPlacementRepo(
        active_placement=RackPlacement(
            rack_code="RACK-001",
            location_code="LOC-OLD",
            placement_status="ARRIVED",
            source_system=ResourceSourceSystem.WMS,
            source_event_id="old-event",
            started_at=datetime(2026, 5, 16, 7, 0, 0),
        )
    )
    service = ResourceRelationService(
        state_event_repo=state_events,
        rack_placement_repo=placements,
        runtime_hold_creator=runtime_holds,
    )

    result = await service.record_rack_arrived(
        object(),
        rack_code="RACK-001",
        location_code="LOC-NEW",
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-002",
        occurred_at=datetime(2026, 5, 16, 8, 0, 0),
        trace_id="trace-001",
        session_id="session-001",
        workline_id=1001,
        workline_session_id=2001,
        plugin_key="smt_classifier",
        contract_version="1.0",
    )

    assert result.status == ResourceProjectionStatus.RECONCILING
    assert result.reason_code == "RACK_PLACEMENT_CONFLICT"
    assert result.runtime_hold is not None
    assert result.runtime_hold.id == 9001
    assert state_events.created[0]["resource_code"] == "RACK-001"
    assert runtime_holds.created[0]["source_reason"] == "RACK_PLACEMENT_CONFLICT"
    assert runtime_holds.created[0]["source_event_id"] == "wms-event-002"
    assert runtime_holds.created[0]["evidence"]["active_location_code"] == "LOC-OLD"
    assert runtime_holds.created[0]["evidence"]["incoming_location_code"] == "LOC-NEW"
    assert placements.created == []
