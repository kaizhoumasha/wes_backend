"""资源关系服务测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import RackBinMount, RackPlacement, ResourceSourceSystem, ResourceStateEvent


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


class RecordingBinMountRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def get_active_by_rack_slot(
        self,
        _db: object,
        *,
        rack_code: str,
        rack_slot_code: str,
    ) -> RackBinMount | None:
        assert rack_code == "RACK-002"
        assert rack_slot_code in {"A01", "A02"}
        return None

    async def get_active_by_bin_code(self, _db: object, bin_code: str) -> RackBinMount | None:
        assert bin_code in {"BIN-001", "BIN-002"}
        return None

    async def create(self, _db: object, data: dict[str, Any]) -> RackBinMount:
        self.created.append(data)
        return RackBinMount(**data)


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


@pytest.mark.asyncio
async def test_record_full_box_exchange_physical_completed_projects_bin_mounts() -> None:
    """可信满箱交换后关系写资源事实，并创建 active RackBinMount 投影。"""

    from src.app.resource.services import ResourceProjectionStatus, ResourceRelationService

    state_events = RecordingStateEventRepo()
    bin_mounts = RecordingBinMountRepo()
    service = ResourceRelationService(state_event_repo=state_events, rack_bin_mount_repo=bin_mounts)

    result = await service.record_full_box_exchange_physical_completed(
        object(),
        exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE",
        rack_release_id="release-001",
        post_exchange_relations={
            "bin_mounts": [
                {"rack_code": "RACK-002", "rack_slot_code": "A01", "bin_code": "BIN-001"},
                {"rack_code": "RACK-002", "rack_slot_code": "A02", "bin_code": "BIN-002"},
            ]
        },
        source_system=ResourceSourceSystem.WMS,
        source_event_id="wms-event-physical-001",
        source_version="1",
        source_task_id="wms-task-001",
        occurred_at=datetime(2026, 5, 16, 9, 0, 0),
        trace_id="trace-001",
        session_id="session-001",
    )

    assert result.status == ResourceProjectionStatus.PROJECTED
    assert state_events.created[0]["event_type"] == "EXCHANGE_STATUS_UPDATED"
    assert state_events.created[0]["resource_type"] == "EXCHANGE_TASK"
    assert state_events.created[0]["resource_code"] == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert len(bin_mounts.created) == 2
    assert bin_mounts.created[0]["rack_code"] == "RACK-002"
    assert bin_mounts.created[0]["rack_slot_code"] == "A01"
    assert bin_mounts.created[0]["bin_code"] == "BIN-001"
    assert bin_mounts.created[0]["mount_status"] == "MOUNTED"
    assert bin_mounts.created[0]["source_system"] == "WMS_RCS"
