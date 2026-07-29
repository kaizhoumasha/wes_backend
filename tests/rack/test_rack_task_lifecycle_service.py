from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.rack.models import RackTaskStatus
from src.app.rack.services import RackTaskLifecycleService


class FakeRackTaskRepository:
    def __init__(self) -> None:
        self.by_task_key: dict[str, SimpleNamespace] = {}
        self.by_dispatch_key: dict[str, SimpleNamespace] = {}
        self.by_operation: dict[str, list[SimpleNamespace]] = {}
        self.created: list[dict[str, Any]] = []

    async def get_by_task_key(self, _db: Any, task_key: str) -> SimpleNamespace | None:
        return self.by_task_key.get(task_key)

    async def get_by_dispatch_key(self, _db: Any, dispatch_key: str) -> SimpleNamespace | None:
        return self.by_dispatch_key.get(dispatch_key)

    async def get_by_operation_sequence(
        self,
        _db: Any,
        *,
        operation_key: str,
        sequence_no: int,
    ) -> SimpleNamespace | None:
        return next(
            (task for task in self.by_operation.get(operation_key, ()) if task.sequence_no == sequence_no),
            None,
        )

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        task = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        self.add_existing(task)
        return task

    def add_existing(self, task: SimpleNamespace) -> None:
        self.by_task_key[task.task_key] = task
        self.by_dispatch_key[task.dispatch_key] = task
        self.by_operation.setdefault(task.operation_key, []).append(task)


def _request_kwargs() -> dict[str, Any]:
    return {
        "session": SimpleNamespace(id=300),
        "workline": SimpleNamespace(id=45, line_code="WL-SMT-01"),
        "outbox": SimpleNamespace(id=None),
        "operation_key": "rack-op:trace-001",
        "operation_type": "SMT_EMPTY_RACK_REPLENISHMENT",
        "sequence_no": 1,
        "task_type": "ALLOCATE_AND_MOVE_RACK",
        "task_key": "rack-task:trace-001:allocate-empty:1",
        "dispatch_key": "dispatch:trace-001:allocate-empty:1",
        "target_code": "http://wms-rcs/api/rack-operation",
        "request_json": {
            "request_type": "SMT_RACK_OPERATION",
            "target_position_code": "SINGLE_LAYER_A",
        },
        "timeout_seconds": 1800,
        "source_system": "WMS_RCS",
        "trace_id": "trace-001",
        "rack_kind": "EMPTY_BIN",
        "target_position_code": "SINGLE_LAYER_A",
        "target_position_role": "INBOUND_BUFFER",
        "actions_json": {"actions": [{"type": "ALLOCATE_AND_MOVE_RACK"}]},
    }


@pytest.mark.asyncio
async def test_record_requested_task_creates_low_level_task_idempotently() -> None:
    repo = FakeRackTaskRepository()
    service = RackTaskLifecycleService(rack_task_repository=repo)
    request = _request_kwargs()

    first = await service.record_requested_task(SimpleNamespace(), **request)
    second = await service.record_requested_task(SimpleNamespace(), **request)

    assert first is second
    assert len(repo.created) == 1
    assert first.task_status == RackTaskStatus.REQUESTED.value
    assert first.material_session_id == 300
    assert first.target_position_code == "SINGLE_LAYER_A"
    assert first.actions_json == {"actions": [{"type": "ALLOCATE_AND_MOVE_RACK"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"operation_key": "rack-op:trace-002"},
        {"operation_type": "SMT_RACK_RELOCATION"},
        {"task_type": "MOVE_RACK"},
        {"dispatch_key": "dispatch:other"},
    ],
)
async def test_record_requested_task_rejects_task_key_identity_drift(mutation: dict[str, Any]) -> None:
    repo = FakeRackTaskRepository()
    service = RackTaskLifecycleService(rack_task_repository=repo)
    request = _request_kwargs()
    await service.record_requested_task(SimpleNamespace(), **request)

    with pytest.raises(ValueError, match="task_key 已绑定不同 rack task"):
        await service.record_requested_task(SimpleNamespace(), **{**request, **mutation})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"operation_key": ""},
        {"operation_type": " "},
        {"sequence_no": 0},
        {"sequence_no": -1},
    ],
)
async def test_record_requested_task_rejects_invalid_operation_metadata(mutation: dict[str, Any]) -> None:
    service = RackTaskLifecycleService(rack_task_repository=FakeRackTaskRepository())

    with pytest.raises(ValueError, match="operation"):
        await service.record_requested_task(SimpleNamespace(), **{**_request_kwargs(), **mutation})


@pytest.mark.asyncio
async def test_record_requested_task_rejects_operation_sequence_or_dispatch_conflict() -> None:
    repo = FakeRackTaskRepository()
    service = RackTaskLifecycleService(rack_task_repository=repo)
    request = _request_kwargs()
    await service.record_requested_task(SimpleNamespace(), **request)

    with pytest.raises(ValueError, match="operation sequence"):
        await service.record_requested_task(
            SimpleNamespace(),
            **{
                **request,
                "task_key": "rack-task:other",
                "dispatch_key": "dispatch:other",
                "task_type": "MOVE_RACK",
            },
        )

    with pytest.raises(ValueError, match="dispatch_key 已绑定不同 rack task"):
        await service.record_requested_task(
            SimpleNamespace(),
            **{
                **request,
                "sequence_no": 2,
                "task_key": "rack-task:other",
                "task_type": "MOVE_RACK",
            },
        )
