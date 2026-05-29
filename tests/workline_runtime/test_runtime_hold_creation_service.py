"""RuntimeHold 创建服务测试。"""

from types import SimpleNamespace
from typing import Any

import pytest


class RecordingRuntimeHoldRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_open_hold(self, _db: object, **data: Any) -> SimpleNamespace:
        self.created.append(data)
        return SimpleNamespace(id=8101, **data)


class RecordingWorkLineRepository:
    def __init__(self, workline: SimpleNamespace | None) -> None:
        self.workline = workline
        self.locked_ids: list[int] = []

    async def get_for_update(self, _db: object, workline_id: int) -> SimpleNamespace | None:
        self.locked_ids.append(workline_id)
        return self.workline


@pytest.mark.asyncio
async def test_create_for_resource_reconciliation_records_runtime_hold_evidence() -> None:
    """资源冲突应幂等创建 RuntimeHold，并保留冲突证据快照。"""

    from src.app.workline.models.runtime_hold import RuntimeHoldType
    from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService

    repository = RecordingRuntimeHoldRepository()
    service = RuntimeHoldCreationService(repository=repository, workline_repository=RecordingWorkLineRepository(None))

    hold = await service.create_for_resource_reconciliation(
        object(),
        workline_id=1001,
        session_id=2001,
        trace_id="trace-001",
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        source_reason="RACK_PLACEMENT_CONFLICT",
        source_event_id="wms-event-002",
        evidence={
            "rack_code": "RACK-001",
            "active_location_code": "LOC-OLD",
            "incoming_location_code": "LOC-NEW",
        },
    )

    assert hold.id == 8101
    assert repository.created[0]["hold_type"] == RuntimeHoldType.RUNTIME_RECONCILIATION
    assert repository.created[0]["source_kind"] == "RESOURCE_RECONCILIATION"
    assert repository.created[0]["source_reason"] == "RACK_PLACEMENT_CONFLICT"
    assert repository.created[0]["source_idempotency_key"] == (
        "resource-reconciliation:RACK_PLACEMENT_CONFLICT:wms-event-002"
    )
    assert repository.created[0]["evidence_snapshot_json"]["rack_code"] == "RACK-001"


@pytest.mark.asyncio
async def test_create_for_resource_reconciliation_projects_workline_reconciling_and_blocks_accepting_work() -> None:
    """资源冲突 hold 必须同步冻结 WorkLine，否则后续安全门禁仍会接受新任务。"""

    from src.app.workline.models.safety import WorkLineRuntimeStatus
    from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService
    from src.app.workline.services.safety_service import WorkLineSafetyBlocked, WorkLineSafetyService

    repository = RecordingRuntimeHoldRepository()
    workline = SimpleNamespace(
        id=1001,
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
    )
    workline_repository = RecordingWorkLineRepository(workline)
    service = RuntimeHoldCreationService(repository=repository, workline_repository=workline_repository)

    _ = await service.create_for_resource_reconciliation(
        object(),
        workline_id=1001,
        session_id=2001,
        trace_id="trace-001",
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        source_reason="BIN_CELL_RESERVATION_CONFLICT",
        source_event_id="reserve-event-001",
        evidence={"bin_code": "BIN-001", "bin_cell_index": "4"},
    )

    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_at is not None
    assert workline.stopped_reason == "BIN_CELL_RESERVATION_CONFLICT"
    safety_service = WorkLineSafetyService(workline_repository=workline_repository)
    with pytest.raises(WorkLineSafetyBlocked, match="WORKLINE_RECONCILING"):
        await safety_service.assert_accepting_work(object(), workline_id=1001)
