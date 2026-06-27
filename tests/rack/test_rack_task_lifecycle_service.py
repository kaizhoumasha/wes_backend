from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

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
        for task in self.by_operation.get(operation_key, []):
            if task.sequence_no == sequence_no:
                return task
        return None

    async def list_by_operation_key(self, _db: Any, *, operation_key: str) -> list[SimpleNamespace]:
        return sorted(self.by_operation.get(operation_key, []), key=lambda task: task.sequence_no)

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        task = SimpleNamespace(id=len(self.created) + 1, **data)
        self.created.append(data)
        self.by_task_key[task.task_key] = task
        self.by_dispatch_key[task.dispatch_key] = task
        self.by_operation.setdefault(task.operation_key, []).append(task)
        return task

    def add_existing(self, task: SimpleNamespace) -> None:
        self.by_task_key[task.task_key] = task
        self.by_dispatch_key[task.dispatch_key] = task
        self.by_operation.setdefault(task.operation_key, []).append(task)


class FakeSessionRepository:
    def __init__(self) -> None:
        self.by_operation: dict[tuple[int, str], SimpleNamespace] = {}

    async def get_open_session_by_waiting_rack_operation_key(
        self,
        _db: Any,
        *,
        workline_id: int,
        operation_key: str,
    ) -> SimpleNamespace | None:
        return self.by_operation.get((workline_id, operation_key))


class FakeRackOperationService:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[str] = []

    async def derive_operation_status(self, _db: Any, *, operation_key: str) -> str:
        self.calls.append(operation_key)
        return self.status


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.finished_dispatch_keys: list[str] = []

    async def finish_sent_external_by_dispatch_key(self, _db: Any, dispatch_key: str) -> object | None:
        self.finished_dispatch_keys.append(dispatch_key)
        return SimpleNamespace(dispatch_key=dispatch_key)


@pytest.mark.asyncio
async def test_record_requested_task_creates_low_level_task_idempotently() -> None:
    repo = FakeRackTaskRepository()
    service = RackTaskLifecycleService(rack_task_repository=repo)
    db = SimpleNamespace()
    session = SimpleNamespace(id=300, workline_id=45)
    workline = SimpleNamespace(id=45, line_code="WL-SMT-01")
    outbox = SimpleNamespace(id=None)

    first = await service.record_requested_task(
        db,
        session=session,
        workline=workline,
        outbox=outbox,
        operation_key="rack-op:trace-001",
        operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
        sequence_no=1,
        task_type="ALLOCATE_AND_MOVE_RACK",
        task_key="rack-task:trace-001:allocate-empty:1",
        dispatch_key="dispatch:trace-001:allocate-empty:1",
        target_code="http://wms-rcs/api/rack-operation",
        request_json={"request_type": "SMT_RACK_OPERATION", "target_position_code": "SINGLE_LAYER_A"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
        trace_id="trace-001",
        rack_kind="EMPTY_BIN",
        target_position_code="SINGLE_LAYER_A",
        target_position_role="INBOUND_BUFFER",
        actions_json={"actions": [{"type": "ALLOCATE_AND_MOVE_RACK"}]},
    )
    second = await service.record_requested_task(
        db,
        session=session,
        workline=workline,
        outbox=outbox,
        operation_key="rack-op:trace-001",
        operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
        sequence_no=1,
        task_type="ALLOCATE_AND_MOVE_RACK",
        task_key="rack-task:trace-001:allocate-empty:1",
        dispatch_key="dispatch:trace-001:allocate-empty:1",
        target_code="http://wms-rcs/api/rack-operation",
        request_json={"request_type": "SMT_RACK_OPERATION"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
        trace_id="trace-001",
    )

    assert first is second
    assert len(repo.created) == 1
    assert first.task_status == RackTaskStatus.REQUESTED.value
    assert first.operation_key == "rack-op:trace-001"
    assert first.operation_type == "SMT_EMPTY_RACK_REPLENISHMENT"
    assert first.sequence_no == 1
    assert first.task_type == "ALLOCATE_AND_MOVE_RACK"
    assert first.material_session_id == 300
    assert first.target_position_code == "SINGLE_LAYER_A"
    assert first.actions_json == {"actions": [{"type": "ALLOCATE_AND_MOVE_RACK"}]}


@pytest.mark.asyncio
async def test_record_requested_task_rejects_same_task_key_with_different_operation() -> None:
    repo = FakeRackTaskRepository()
    existing = SimpleNamespace(
        task_key="rack-task:trace-001:allocate-empty:1",
        dispatch_key="dispatch:trace-001:allocate-empty:1",
        operation_key="rack-op:trace-001",
        operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
        sequence_no=1,
        task_type="ALLOCATE_AND_MOVE_RACK",
    )
    repo.add_existing(existing)
    service = RackTaskLifecycleService(rack_task_repository=repo)

    with pytest.raises(ValueError, match="task_key 已绑定不同 rack task"):
        await service.record_requested_task(
            SimpleNamespace(),
            session=SimpleNamespace(id=300),
            workline=SimpleNamespace(id=45, line_code="WL-SMT-01"),
            outbox=SimpleNamespace(id=None),
            operation_key="rack-op:trace-002",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="ALLOCATE_AND_MOVE_RACK",
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            target_code="http://wms-rcs/api/rack-operation",
            request_json={"request_type": "SMT_RACK_OPERATION"},
            timeout_seconds=1800,
            source_system="WMS_RCS",
            trace_id="trace-002",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation_key": ""},
        {"operation_type": " "},
        {"sequence_no": 0},
        {"sequence_no": -1},
    ],
)
async def test_record_requested_task_rejects_invalid_operation_metadata(kwargs: dict[str, Any]) -> None:
    repo = FakeRackTaskRepository()
    service = RackTaskLifecycleService(rack_task_repository=repo)
    request_kwargs: dict[str, Any] = {
        "operation_key": "rack-op:trace-001",
        "operation_type": "SMT_EMPTY_RACK_REPLENISHMENT",
        "sequence_no": 1,
    }
    request_kwargs.update(kwargs)

    with pytest.raises(ValueError, match="operation"):
        await service.record_requested_task(
            SimpleNamespace(),
            session=SimpleNamespace(id=300),
            workline=SimpleNamespace(id=45, line_code="WL-SMT-01"),
            outbox=SimpleNamespace(id=None),
            task_type="ALLOCATE_AND_MOVE_RACK",
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            target_code="http://wms-rcs/api/rack-operation",
            request_json={"request_type": "SMT_RACK_OPERATION"},
            **request_kwargs,
        )


@pytest.mark.asyncio
async def test_record_requested_task_rejects_same_operation_sequence_with_different_task() -> None:
    repo = FakeRackTaskRepository()
    repo.add_existing(
        SimpleNamespace(
            task_key="rack-task:trace-001:move-out:1",
            dispatch_key="dispatch:trace-001:move-out:1",
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="MOVE_RACK",
        )
    )
    service = RackTaskLifecycleService(rack_task_repository=repo)

    with pytest.raises(ValueError, match="operation sequence"):
        await service.record_requested_task(
            SimpleNamespace(),
            session=SimpleNamespace(id=300),
            workline=SimpleNamespace(id=45, line_code="WL-SMT-01"),
            outbox=SimpleNamespace(id=None),
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="ALLOCATE_AND_MOVE_RACK",
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            target_code="http://wms-rcs/api/rack-operation",
            request_json={"request_type": "SMT_RACK_OPERATION"},
        )


@pytest.mark.asyncio
async def test_record_requested_task_rejects_same_task_key_with_different_operation_type() -> None:
    repo = FakeRackTaskRepository()
    repo.add_existing(
        SimpleNamespace(
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="ALLOCATE_AND_MOVE_RACK",
        )
    )
    service = RackTaskLifecycleService(rack_task_repository=repo)

    with pytest.raises(ValueError, match="task_key 已绑定不同 rack task"):
        await service.record_requested_task(
            SimpleNamespace(),
            session=SimpleNamespace(id=300),
            workline=SimpleNamespace(id=45, line_code="WL-SMT-01"),
            outbox=SimpleNamespace(id=None),
            operation_key="rack-op:trace-001",
            operation_type="SMT_RACK_RELOCATION",
            sequence_no=1,
            task_type="ALLOCATE_AND_MOVE_RACK",
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            target_code="http://wms-rcs/api/rack-operation",
            request_json={"request_type": "SMT_RACK_OPERATION"},
        )


@pytest.mark.asyncio
async def test_record_requested_task_rejects_same_task_key_with_different_task_type() -> None:
    repo = FakeRackTaskRepository()
    repo.add_existing(
        SimpleNamespace(
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="ALLOCATE_AND_MOVE_RACK",
        )
    )
    service = RackTaskLifecycleService(rack_task_repository=repo)

    with pytest.raises(ValueError, match="task_key 已绑定不同 rack task"):
        await service.record_requested_task(
            SimpleNamespace(),
            session=SimpleNamespace(id=300),
            workline=SimpleNamespace(id=45, line_code="WL-SMT-01"),
            outbox=SimpleNamespace(id=None),
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="MOVE_RACK",
            task_key="rack-task:trace-001:allocate-empty:1",
            dispatch_key="dispatch:trace-001:allocate-empty:1",
            target_code="http://wms-rcs/api/rack-operation",
            request_json={"request_type": "SMT_RACK_OPERATION"},
        )


@pytest.mark.asyncio
async def test_record_requested_task_rejects_dispatch_key_bound_to_other_task() -> None:
    repo = FakeRackTaskRepository()
    repo.add_existing(
        SimpleNamespace(
            task_key="rack-task:trace-001:move-out:1",
            dispatch_key="dispatch:trace-001:shared",
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=1,
            task_type="MOVE_RACK",
        )
    )
    service = RackTaskLifecycleService(rack_task_repository=repo)

    with pytest.raises(ValueError, match="dispatch_key 已绑定不同 rack task"):
        await service.record_requested_task(
            SimpleNamespace(),
            session=SimpleNamespace(id=300),
            workline=SimpleNamespace(id=45, line_code="WL-SMT-01"),
            outbox=SimpleNamespace(id=None),
            operation_key="rack-op:trace-001",
            operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
            sequence_no=2,
            task_type="ALLOCATE_AND_MOVE_RACK",
            task_key="rack-task:trace-001:allocate-empty:2",
            dispatch_key="dispatch:trace-001:shared",
            target_code="http://wms-rcs/api/rack-operation",
            request_json={"request_type": "SMT_RACK_OPERATION"},
        )


@pytest.mark.asyncio
async def test_record_callback_updates_single_task_status_only() -> None:
    repo = FakeRackTaskRepository()
    outbox_repo = FakeOutboxRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:1"
    task = SimpleNamespace(
        id=1,
        task_key="rack-task:trace-001:allocate-empty:1",
        operation_key="rack-op:trace-001",
        sequence_no=1,
        dispatch_key=dispatch_key,
        outbox_id=11,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    service = RackTaskLifecycleService(rack_task_repository=repo, outbox_repository=outbox_repo)
    db = SimpleNamespace(add=MagicMock())

    updated = await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "WMS_RACK_TASK_RESULT",
            "dispatch_key": dispatch_key,
            "status": "SUCCEEDED",
            "active_bin_rack": {"rack_code": "RACK-001"},
        },
        trace_id="trace-001",
    )

    assert updated is task
    assert task.task_status == RackTaskStatus.SUCCEEDED
    assert task.callback_json["active_bin_rack"]["rack_code"] == "RACK-001"
    assert task.result_json == {"task_status": "SUCCEEDED"}
    assert task.trace_id == "trace-001"
    assert task.completed_at is not None
    db.add.assert_called_once_with(task)
    assert outbox_repo.finished_dispatch_keys == [dispatch_key]


@pytest.mark.asyncio
async def test_record_callback_keeps_sent_outbox_open_for_progress_status() -> None:
    repo = FakeRackTaskRepository()
    outbox_repo = FakeOutboxRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:1"
    task = SimpleNamespace(
        id=1,
        task_key="rack-task:trace-001:allocate-empty:1",
        operation_key="rack-op:trace-001",
        sequence_no=1,
        dispatch_key=dispatch_key,
        outbox_id=11,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    service = RackTaskLifecycleService(rack_task_repository=repo, outbox_repository=outbox_repo)
    db = SimpleNamespace(add=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "WMS_RACK_TASK_RESULT",
            "dispatch_key": dispatch_key,
            "status": "PHYSICAL_COMPLETED",
        },
        trace_id="trace-001",
    )

    assert task.task_status == RackTaskStatus.IN_PROGRESS
    assert outbox_repo.finished_dispatch_keys == []


@pytest.mark.asyncio
async def test_record_callback_finishes_sent_outbox_for_existing_terminal_task() -> None:
    repo = FakeRackTaskRepository()
    outbox_repo = FakeOutboxRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:1"
    task = SimpleNamespace(
        id=1,
        task_key="rack-task:trace-001:allocate-empty:1",
        operation_key="rack-op:trace-001",
        sequence_no=1,
        dispatch_key=dispatch_key,
        outbox_id=11,
        task_status=RackTaskStatus.SUCCEEDED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=object(),
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    service = RackTaskLifecycleService(rack_task_repository=repo, outbox_repository=outbox_repo)
    db = SimpleNamespace(add=MagicMock())

    updated = await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "WMS_RACK_TASK_RESULT",
            "dispatch_key": dispatch_key,
            "status": "SUCCEEDED",
        },
        trace_id="trace-001",
    )

    assert updated is task
    assert outbox_repo.finished_dispatch_keys == [dispatch_key]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_type", ["WMS_RACK_EXCHANGE_FAILED", "RCS_RACK_EXCHANGE_FAILED"])
async def test_record_callback_maps_rack_exchange_failed_to_failed_with_raw_error_code(
    callback_type: str,
) -> None:
    repo = FakeRackTaskRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:1"
    task = SimpleNamespace(
        id=1,
        task_key="rack-task:trace-001:allocate-empty:1",
        operation_key="rack-op:trace-001",
        operation_type="SMT_EMPTY_RACK_REPLENISHMENT",
        sequence_no=1,
        task_type="ALLOCATE_AND_MOVE_RACK",
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    service = RackTaskLifecycleService(rack_task_repository=repo)
    db = SimpleNamespace(add=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": callback_type,
            "dispatch_key": dispatch_key,
            "reason_code": "RCS_RACK_OPERATION_FAILED",
            "reason_message": "外部系统拒绝",
        },
        trace_id="trace-001",
    )

    assert task.task_status == RackTaskStatus.FAILED
    assert task.error_code == "RCS_RACK_OPERATION_FAILED"
    assert task.result_json["external_error_code"] == "RCS_RACK_OPERATION_FAILED"
    assert task.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_status", "expected_status"),
    [
        ("BUSINESS_COMPLETED", RackTaskStatus.SUCCEEDED),
        ("PHYSICAL_COMPLETED", RackTaskStatus.IN_PROGRESS),
        ("FAILED", RackTaskStatus.FAILED),
        ("WMS_REJECTED", RackTaskStatus.FAILED),
        ("FAILED_AGV", RackTaskStatus.FAILED),
        ("FAILED_CTU", RackTaskStatus.FAILED),
        ("REJECTED_EXCHANGE_AREA_FULL", RackTaskStatus.FAILED),
        ("REJECTED_EMPTY_BIN_UNAVAILABLE", RackTaskStatus.FAILED),
        ("FAILED_VENDOR_SPECIFIC", RackTaskStatus.FAILED),
        ("TIMEOUT", RackTaskStatus.TIMEOUT),
        ("RESOURCE_PROJECTION_UNCONFIRMED", RackTaskStatus.RECONCILING),
    ],
)
async def test_record_callback_maps_external_status_to_canonical_task_status(
    payload_status: str,
    expected_status: RackTaskStatus,
) -> None:
    repo = FakeRackTaskRepository()
    dispatch_key = f"dispatch:trace-001:{payload_status.lower()}"
    task = SimpleNamespace(
        id=1,
        task_key=f"rack-task:trace-001:{payload_status.lower()}",
        operation_key=f"rack-op:trace-001:{payload_status.lower()}",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type="ALLOCATE_AND_MOVE_RACK",
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    service = RackTaskLifecycleService(rack_task_repository=repo)

    await service.record_callback_from_external_http(
        SimpleNamespace(add=MagicMock()),
        payload_json={"dispatch_key": dispatch_key, "status": payload_status},
    )

    assert task.task_status == expected_status


@pytest.mark.asyncio
async def test_record_callback_does_not_regress_terminal_task_status() -> None:
    repo = FakeRackTaskRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:1"
    task = SimpleNamespace(
        id=1,
        task_key="rack-task:trace-001:allocate-empty:1",
        operation_key="rack-op:trace-001",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type="ALLOCATE_AND_MOVE_RACK",
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.SUCCEEDED,
        callback_json={"status": "SUCCEEDED"},
        result_json={"task_status": "SUCCEEDED"},
        trace_id="trace-original",
        started_at=None,
        completed_at=object(),
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    service = RackTaskLifecycleService(rack_task_repository=repo)
    db = SimpleNamespace(add=MagicMock())

    updated = await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "WMS_RACK_TASK_PROGRESS",
            "dispatch_key": dispatch_key,
            "status": "IN_PROGRESS",
        },
        trace_id="trace-late",
    )

    assert updated is task
    assert task.task_status == RackTaskStatus.SUCCEEDED
    assert task.callback_json == {"status": "SUCCEEDED"}
    assert task.result_json == {"task_status": "SUCCEEDED"}
    assert task.trace_id == "trace-original"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_record_callback_keeps_operation_incomplete_when_sibling_pending() -> None:
    repo = FakeRackTaskRepository()
    completed = SimpleNamespace(
        id=1,
        task_key="rack-task:trace-001:move-out:1",
        operation_key="rack-op:trace-001",
        sequence_no=1,
        dispatch_key="dispatch:trace-001:move-out:1",
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    pending = SimpleNamespace(
        id=2,
        task_key="rack-task:trace-001:allocate-empty:2",
        operation_key="rack-op:trace-001",
        sequence_no=2,
        dispatch_key="dispatch:trace-001:allocate-empty:2",
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(completed)
    repo.add_existing(pending)
    service = RackTaskLifecycleService(rack_task_repository=repo)
    db = SimpleNamespace(add=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={
            "dispatch_key": completed.dispatch_key,
            "status": "SUCCEEDED",
        },
        trace_id="trace-001",
    )

    assert completed.task_status == RackTaskStatus.SUCCEEDED
    assert pending.task_status == RackTaskStatus.REQUESTED
    assert completed.result_json == {"task_status": "SUCCEEDED"}
    assert "operation_status" not in completed.result_json


@pytest.mark.asyncio
async def test_record_callback_resumes_session_only_when_operation_succeeded() -> None:
    repo = FakeRackTaskRepository()
    session_repo = FakeSessionRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:2"
    task = SimpleNamespace(
        id=2,
        task_key="rack-task:trace-001:allocate-empty:2",
        operation_key="rack-op:trace-001",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=2,
        task_type="ALLOCATE_AND_MOVE_RACK",
        workline_id=45,
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    session = SimpleNamespace(
        id=300,
        status="WAITING_EXTERNAL",
        current_wait_type="RACK_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=300,
        awaiting_device_command_code=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        context_json={
            "waiting_rack_operation_key": "rack-op:trace-001",
            "rack_operation": {"operation_key": "rack-op:trace-001", "status": "PENDING"},
        },
    )
    session_repo.by_operation[(45, "rack-op:trace-001")] = session
    operation_service = FakeRackOperationService("SUCCEEDED")
    service = RackTaskLifecycleService(
        rack_task_repository=repo,
        session_repository=session_repo,  # type: ignore[arg-type]
        rack_operation_service=operation_service,
    )
    db = SimpleNamespace(add=MagicMock(), flush=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={"dispatch_key": dispatch_key, "status": "SUCCEEDED"},
        trace_id="trace-001",
    )

    assert operation_service.calls == ["rack-op:trace-001"]
    assert session.status == "RUNNING"
    assert session.current_wait_type is None
    assert session.waiting_since is None
    assert session.deadline_at is None
    assert session.current_wait_timeout_seconds is None
    assert session.context_json["waiting_rack_operation_key"] is None
    assert session.context_json["rack_operation"]["status"] == "SUCCEEDED"
    assert session.failure_domain is None
    assert session.failure_code is None
    assert db.add.call_args_list[-1].args == (session,)


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_type", ["WMS_RACK_ARRIVED", "RCS_RACK_ARRIVED"])
async def test_record_callback_defers_arrived_operation_sync_until_resource_projection(
    callback_type: str,
) -> None:
    repo = FakeRackTaskRepository()
    session_repo = FakeSessionRepository()
    dispatch_key = "rack-operation:rack-op:trace-001:2:ALLOCATE_AND_MOVE_RACK"
    task = SimpleNamespace(
        id=2,
        task_key=dispatch_key,
        operation_key="rack-op:trace-001",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=2,
        task_type="ALLOCATE_AND_MOVE_RACK",
        workline_id=45,
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    session = SimpleNamespace(
        id=300,
        status="WAITING_EXTERNAL",
        current_wait_type="RACK_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=300,
        awaiting_device_command_code=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        context_json={
            "waiting_rack_operation_key": "rack-op:trace-001",
            "rack_operation": {"operation_key": "rack-op:trace-001", "status": "PENDING"},
        },
    )
    session_repo.by_operation[(45, "rack-op:trace-001")] = session
    operation_service = FakeRackOperationService("RECONCILING")
    service = RackTaskLifecycleService(
        rack_task_repository=repo,
        session_repository=session_repo,  # type: ignore[arg-type]
        rack_operation_service=operation_service,
    )
    db = SimpleNamespace(add=MagicMock(), flush=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": callback_type,
            "dispatch_key": dispatch_key,
            "active_bin_rack": {"rack_code": "RACK-NEW"},
        },
        trace_id="trace-001",
    )

    assert task.task_status == RackTaskStatus.SUCCEEDED
    assert task.result_json == {"task_status": "SUCCEEDED"}
    assert operation_service.calls == []
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.context_json["waiting_rack_operation_key"] == "rack-op:trace-001"
    assert session.context_json["rack_operation"]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_record_callback_holds_session_when_operation_failed() -> None:
    repo = FakeRackTaskRepository()
    session_repo = FakeSessionRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:2"
    task = SimpleNamespace(
        id=2,
        task_key="rack-task:trace-001:allocate-empty:2",
        operation_key="rack-op:trace-001",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=2,
        task_type="ALLOCATE_AND_MOVE_RACK",
        workline_id=45,
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    session = SimpleNamespace(
        id=300,
        status="WAITING_EXTERNAL",
        current_wait_type="RACK_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=300,
        awaiting_device_command_code=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        context_json={
            "waiting_rack_operation_key": "rack-op:trace-001",
            "rack_operation": {"operation_key": "rack-op:trace-001", "status": "PENDING"},
        },
    )
    session_repo.by_operation[(45, "rack-op:trace-001")] = session
    service = RackTaskLifecycleService(
        rack_task_repository=repo,
        session_repository=session_repo,  # type: ignore[arg-type]
        rack_operation_service=FakeRackOperationService("FAILED"),
    )
    db = SimpleNamespace(add=MagicMock(), flush=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={
            "dispatch_key": dispatch_key,
            "status": "FAILED",
            "reason_code": "RCS_RACK_OPERATION_FAILED",
            "reason_message": "外部系统拒绝",
        },
        trace_id="trace-001",
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.waiting_since is None
    assert session.deadline_at is None
    assert session.current_wait_timeout_seconds is None
    assert session.awaiting_device_command_code is None
    assert session.failure_domain == "EXTERNAL"
    assert session.failure_code == "RCS_RACK_OPERATION_FAILED"
    assert session.failure_message == "外部系统拒绝"
    assert session.context_json["rack_operation"]["status"] == "FAILED"
    assert session.context_json["rack_operation"]["reason_code"] == "RCS_RACK_OPERATION_FAILED"


@pytest.mark.asyncio
async def test_record_callback_preserves_failure_reason_when_later_sibling_succeeds() -> None:
    repo = FakeRackTaskRepository()
    session_repo = FakeSessionRepository()
    dispatch_key = "dispatch:trace-001:allocate-empty:2"
    task = SimpleNamespace(
        id=2,
        task_key="rack-task:trace-001:allocate-empty:2",
        operation_key="rack-op:trace-001",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=2,
        task_type="ALLOCATE_AND_MOVE_RACK",
        workline_id=45,
        dispatch_key=dispatch_key,
        task_status=RackTaskStatus.REQUESTED,
        callback_json={},
        result_json={},
        trace_id=None,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    repo.add_existing(task)
    session = SimpleNamespace(
        id=300,
        status="MANUAL_HOLD",
        current_wait_type="RACK_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=300,
        awaiting_device_command_code=9001,
        failure_domain="EXTERNAL",
        failure_code="RCS_RACK_OPERATION_FAILED",
        failure_message="外部系统拒绝",
        context_json={
            "waiting_rack_operation_key": "rack-op:trace-001",
            "rack_operation": {
                "operation_key": "rack-op:trace-001",
                "status": "FAILED",
                "reason_code": "RCS_RACK_OPERATION_FAILED",
                "message": "外部系统拒绝",
            },
        },
    )
    session_repo.by_operation[(45, "rack-op:trace-001")] = session
    service = RackTaskLifecycleService(
        rack_task_repository=repo,
        session_repository=session_repo,  # type: ignore[arg-type]
        rack_operation_service=FakeRackOperationService("FAILED"),
    )
    db = SimpleNamespace(add=MagicMock(), flush=MagicMock())

    await service.record_callback_from_external_http(
        db,
        payload_json={"dispatch_key": dispatch_key, "status": "SUCCEEDED"},
        trace_id="trace-001",
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.waiting_since is None
    assert session.deadline_at is None
    assert session.current_wait_timeout_seconds is None
    assert session.awaiting_device_command_code is None
    assert session.failure_domain == "EXTERNAL"
    assert session.failure_code == "RCS_RACK_OPERATION_FAILED"
    assert session.failure_message == "外部系统拒绝"
    assert session.context_json["rack_operation"]["status"] == "FAILED"
    assert session.context_json["rack_operation"]["reason_code"] == "RCS_RACK_OPERATION_FAILED"
