from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.app.workline.models.rack_task import WorklineRackTaskStatus
from src.app.workline.services.rack_task_service import WorklineRackTaskService


class FakeRackTaskRepository:
    def __init__(self) -> None:
        self.by_task_key: dict[str, SimpleNamespace] = {}
        self.by_dispatch_key: dict[str, SimpleNamespace] = {}
        self.created: list[dict[str, Any]] = []

    async def get_by_task_key(self, _db: Any, task_key: str) -> SimpleNamespace | None:
        return self.by_task_key.get(task_key)

    async def get_by_dispatch_key(self, _db: Any, dispatch_key: str) -> SimpleNamespace | None:
        return self.by_dispatch_key.get(dispatch_key)

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        task = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        self.by_task_key[task.task_key] = task
        self.by_dispatch_key[task.dispatch_key] = task
        return task


@pytest.mark.asyncio
async def test_record_requested_from_rack_task_request_creates_task_idempotently() -> None:
    repo = FakeRackTaskRepository()
    service = WorklineRackTaskService(rack_task_repository=repo)
    db = SimpleNamespace()
    session = SimpleNamespace(id=300, workline_id=45)
    workline = SimpleNamespace(id=45, line_code="WL-SMT-01")
    outbox = SimpleNamespace(id=None)

    first = await service.record_requested_from_rack_task_request(
        db,
        session=session,
        workline=workline,
        outbox=outbox,
        task_type="RACK_SUPPLY",
        task_key="external:smt_classifier:trace-001:RACK_SUPPLY",
        dispatch_key="external:smt_classifier:trace-001:RACK_SUPPLY",
        target_code="http://wms-rcs/api/rack-supply",
        payload_json={"request_type": "SMT_RACK_SUPPLY", "target_position_code": "SINGLE_LAYER_A"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
        trace_id="trace-001",
    )
    second = await service.record_requested_from_rack_task_request(
        db,
        session=session,
        workline=workline,
        outbox=outbox,
        task_type="RACK_SUPPLY",
        task_key="external:smt_classifier:trace-001:RACK_SUPPLY",
        dispatch_key="external:smt_classifier:trace-001:RACK_SUPPLY",
        target_code="http://wms-rcs/api/rack-supply",
        payload_json={"request_type": "SMT_RACK_SUPPLY"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
        trace_id="trace-001",
    )

    assert first is second
    assert len(repo.created) == 1
    assert first.task_status == WorklineRackTaskStatus.REQUESTED.value
    assert first.material_session_id == 300
    assert first.position_code == "SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_record_callback_from_external_http_updates_task_status() -> None:
    repo = FakeRackTaskRepository()
    dispatch_key = "external:smt_classifier:trace-001:RACK_SUPPLY"
    task = SimpleNamespace(
        id=1,
        dispatch_key=dispatch_key,
        task_status=WorklineRackTaskStatus.REQUESTED,
        callback_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.by_dispatch_key[dispatch_key] = task
    service = WorklineRackTaskService(rack_task_repository=repo)
    db = SimpleNamespace(add=MagicMock())

    updated = await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "WMS_RACK_ARRIVED",
            "dispatch_key": dispatch_key,
            "active_bin_rack": {"rack_code": "RACK-001"},
        },
        trace_id="trace-001",
    )

    assert updated is task
    assert task.task_status == WorklineRackTaskStatus.SUCCEEDED
    assert task.callback_json["active_bin_rack"]["rack_code"] == "RACK-001"
    assert task.trace_id == "trace-001"
    assert task.completed_at is not None
    db.add.assert_called_once_with(task)
