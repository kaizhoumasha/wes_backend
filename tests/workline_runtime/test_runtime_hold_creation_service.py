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


@pytest.mark.asyncio
async def test_create_for_resource_reconciliation_records_runtime_hold_evidence() -> None:
    """资源冲突应幂等创建 RuntimeHold，并保留冲突证据快照。"""

    from src.app.workline.models.runtime_hold import RuntimeHoldType
    from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService

    repository = RecordingRuntimeHoldRepository()
    service = RuntimeHoldCreationService(repository=repository)

    hold = await service.create_for_resource_reconciliation(
        object(),
        workline_id=1001,
        session_id=2001,
        trace_id="trace-001",
        plugin_key="smt_classifier",
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
