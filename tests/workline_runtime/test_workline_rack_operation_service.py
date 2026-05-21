from __future__ import annotations

from types import SimpleNamespace, TracebackType
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from src.app.resource.models import RackKind
from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType, WorklineOutbox
from src.app.workline.models.rack_task import WorklineRackTaskStatus, WorklineRackTaskType
from src.app.workline.models.session import SessionStatus
from src.app.workline.services.rack_operation_service import (
    WorklineRackOperationService,
    WorklineRackOperationStatus,
    WorklineRackOperationType,
)
from src.celery_app.app import celery_app


class FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_count = 0
        self.fail_next_flush_with_integrity = False
        self.nested_rollback_count = 0

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_count += 1
        if self.fail_next_flush_with_integrity:
            self.fail_next_flush_with_integrity = False
            raise IntegrityError("INSERT INTO workline_outbox", {}, Exception("duplicate dispatch_key"))
        for index, item in enumerate(self.added, start=1):
            if getattr(item, "id", None) is None:
                item.id = index

    def begin_nested(self) -> FakeNestedTransaction:
        return FakeNestedTransaction(self)


class FakeNestedTransaction:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.start_len = len(db.added)

    async def __aenter__(self) -> FakeNestedTransaction:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            self.db.nested_rollback_count += 1
            del self.db.added[self.start_len :]
        return False


class FakeRackTaskRepository:
    def __init__(self) -> None:
        self.tasks: list[SimpleNamespace] = []

    async def list_by_operation_key(self, _db: Any, *, operation_key: str) -> list[SimpleNamespace]:
        return sorted(
            [task for task in self.tasks if task.operation_key == operation_key],
            key=lambda task: task.sequence_no,
        )

    async def list_active_by_target_position(
        self,
        _db: Any,
        *,
        workline_code: str,
        target_position_code: str,
    ) -> list[SimpleNamespace]:
        active_statuses = {
            WorklineRackTaskStatus.PLANNED,
            WorklineRackTaskStatus.REQUESTED,
            WorklineRackTaskStatus.IN_PROGRESS,
            WorklineRackTaskStatus.RECONCILING,
            WorklineRackTaskStatus.PLANNED.value,
            WorklineRackTaskStatus.REQUESTED.value,
            WorklineRackTaskStatus.IN_PROGRESS.value,
            WorklineRackTaskStatus.RECONCILING.value,
        }
        return [
            task
            for task in self.tasks
            if task.workline_code == workline_code
            and task.target_position_code == target_position_code
            and task.task_status in active_statuses
        ]

    def add_existing(self, **overrides: Any) -> SimpleNamespace:
        provided_keys = set(overrides)
        values = {
            "id": len(self.tasks) + 1,
            "task_key": f"task-{len(self.tasks) + 1}",
            "dispatch_key": f"dispatch-{len(self.tasks) + 1}",
            "operation_key": "op-001",
            "operation_type": WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
            "sequence_no": len(self.tasks) + 1,
            "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
            "task_status": WorklineRackTaskStatus.REQUESTED,
            "workline_code": "WL-SMT-01",
            "rack_code": None,
            "rack_kind": RackKind.SINGLE_LAYER.value,
            "source_position_code": None,
            "target_position_code": "CLASSIFIER-WORK",
            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
            "target_code": "WMS-RACK",
            "actions_json": {"required": True},
            "request_json": {},
        }
        values.update(overrides)
        deterministic_key = (
            f"rack-operation:{values['operation_key']}:{values['sequence_no']}:{values['task_type'].value}"
        )
        if "dispatch_key" not in provided_keys:
            values["dispatch_key"] = deterministic_key
        if "task_key" not in provided_keys:
            values["task_key"] = deterministic_key
        if "request_json" not in provided_keys:
            values["request_json"] = {
                "operation_key": values["operation_key"],
                "operation_type": values["operation_type"],
                "sequence_no": values["sequence_no"],
                "task_type": values["task_type"].value,
                "source_position_code": values["source_position_code"],
                "target_position_code": values["target_position_code"],
                "target_position_role": values["target_position_role"],
                "rack_kind": values["rack_kind"],
                "rack_code": values["rack_code"],
            }
        task = SimpleNamespace(**values)
        self.tasks.append(task)
        return task


class FakeRackTaskLifecycleService:
    def __init__(self, repository: FakeRackTaskRepository) -> None:
        self.repository = repository
        self.calls: list[dict[str, Any]] = []

    async def record_requested_task(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        task = SimpleNamespace(
            id=len(self.repository.tasks) + 1,
            task_status=WorklineRackTaskStatus.REQUESTED,
            **{
                key: value
                for key, value in kwargs.items()
                if key
                not in {
                    "session",
                    "workline",
                    "outbox",
                    "timeout_seconds",
                    "source_system",
                    "trace_id",
                }
            },
        )
        task.workline_code = kwargs["workline"].line_code
        task.material_session_id = kwargs["session"].id
        self.repository.tasks.append(task)
        return task


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.by_dispatch_key: dict[str, WorklineOutbox] = {}
        self.calls: list[str] = []
        self.return_none_once_for: set[str] = set()

    async def get_by_dispatch_key(self, _db: Any, dispatch_key: str) -> WorklineOutbox | None:
        self.calls.append(dispatch_key)
        if dispatch_key in self.return_none_once_for:
            self.return_none_once_for.remove(dispatch_key)
            return None
        return self.by_dispatch_key.get(dispatch_key)

    def add_existing(self, outbox: WorklineOutbox) -> WorklineOutbox:
        self.by_dispatch_key[outbox.dispatch_key] = outbox
        return outbox


class FakeRackPositionService:
    def __init__(self, *, capacity: int = 1) -> None:
        self.capacity = capacity
        self.calls: list[dict[str, Any]] = []

    async def require_position_capacity_for_update(self, _db: Any, **kwargs: Any) -> tuple[SimpleNamespace, int]:
        self.calls.append(kwargs)
        return (
            SimpleNamespace(
                position_code=kwargs["position_code"],
                position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
                allowed_rack_kind=kwargs["rack_kind"],
                capacity=self.capacity,
            ),
            self.capacity,
        )


class FakeRackPlacementRepository:
    def __init__(
        self,
        *,
        active_placements: list[SimpleNamespace] | None = None,
        active_count: int | None = None,
        placements_by_position: dict[str, list[SimpleNamespace]] | None = None,
    ) -> None:
        self.active_placements = list(active_placements or [])
        self.active_count = active_count
        self.placements_by_position = placements_by_position or {}

    async def list_active_by_workline_position(
        self,
        _db: Any,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[SimpleNamespace]:
        if position_code in self.placements_by_position:
            return list(self.placements_by_position[position_code])
        return list(self.active_placements)

    async def count_active_by_workline_position(
        self,
        _db: Any,
        *,
        workline_code: str,
        position_code: str,
    ) -> int:
        if self.active_count is not None:
            return self.active_count
        return len(
            await self.list_active_by_workline_position(_db, workline_code=workline_code, position_code=position_code)
        )


def _service(
    *,
    active_placements: list[SimpleNamespace] | None = None,
    active_count: int | None = None,
    capacity: int = 1,
    placements_by_position: dict[str, list[SimpleNamespace]] | None = None,
    task_repository: FakeRackTaskRepository | None = None,
    outbox_repository: FakeOutboxRepository | None = None,
) -> tuple[
    WorklineRackOperationService,
    FakeRackTaskRepository,
    FakeRackTaskLifecycleService,
    FakeRackPlacementRepository,
]:
    task_repository = task_repository or FakeRackTaskRepository()
    outbox_repository = outbox_repository or FakeOutboxRepository()
    lifecycle_service = FakeRackTaskLifecycleService(task_repository)
    placement_repository = FakeRackPlacementRepository(
        active_placements=active_placements,
        active_count=active_count,
        placements_by_position=placements_by_position,
    )
    return (
        WorklineRackOperationService(
            rack_task_repository=task_repository,
            rack_task_lifecycle_service=lifecycle_service,
            outbox_repository=outbox_repository,
            rack_position_service=FakeRackPositionService(capacity=capacity),
            rack_placement_repository=placement_repository,
        ),
        task_repository,
        lifecycle_service,
        placement_repository,
    )


def _workline() -> SimpleNamespace:
    return SimpleNamespace(id=45, line_code="WL-SMT-01")


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        id=300,
        status=SessionStatus.RUNNING,
        context_json={"kept": "value"},
        awaiting_command_id=99,
        ended_at="ended",
        failure_domain="PLUGIN",
        failure_code="OLD_FAILURE",
        failure_message="old failure",
    )


def _active_rack(
    rack_code: str = "RACK-OLD",
    *,
    rack_kind: str = RackKind.SINGLE_LAYER.value,
) -> SimpleNamespace:
    return SimpleNamespace(rack_code=rack_code, rack_kind=rack_kind)


@pytest.mark.asyncio
async def test_replace_classifier_work_rack_creates_move_out_and_supply_tasks_with_same_operation_key() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[_active_rack()])
    db = FakeDb()
    session = _session()

    tasks = await service.request_replace_classifier_work_rack(
        db,
        operation_key="op-001",
        workline=_workline(),
        session=session,
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-001",
    )

    assert [task.sequence_no for task in tasks] == [1, 2]
    assert [task.task_type for task in tasks] == [
        WorklineRackTaskType.MOVE_RACK.value,
        WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
    ]
    assert {task.operation_key for task in tasks} == {"op-001"}
    assert tasks[0].rack_code == "RACK-OLD"
    assert tasks[0].source_position_code == "CLASSIFIER-WORK"
    assert tasks[0].target_position_role == "SMT_EMPTY_RACK_AREA"
    assert tasks[1].rack_kind == RackKind.SINGLE_LAYER.value
    assert tasks[1].target_position_code == "CLASSIFIER-WORK"
    assert [call["dispatch_key"] for call in lifecycle.calls] == [
        "rack-operation:op-001:1:MOVE_RACK",
        "rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK",
    ]
    assert [item.status for item in db.added if isinstance(item, WorklineOutbox)] == [
        OutboxStatus.NEW,
        OutboxStatus.NEW,
    ]
    assert session.status == SessionStatus.WAITING_EXTERNAL
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.context_json["waiting_rack_operation_key"] == "op-001"
    assert session.context_json["kept"] == "value"
    assert session.context_json["rack_operation"]["status"] == WorklineRackOperationStatus.PENDING.value
    assert session.context_json["rack_operation"]["task_sequences"] == [1, 2]
    assert session.context_json["rack_operation"]["task_dispatch_keys"] == [
        "rack-operation:op-001:1:MOVE_RACK",
        "rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK",
    ]
    assert session.context_json["rack_operation"]["released_rack_codes"] == ["RACK-OLD"]
    assert session.awaiting_command_id is None
    assert session.ended_at is None
    assert session.failure_domain is None
    assert session.failure_code is None
    assert session.failure_message is None


@pytest.mark.asyncio
async def test_replace_classifier_work_rack_without_current_rack_creates_only_supply_task() -> None:
    service, _repo, _lifecycle, _placements = _service(active_placements=[], active_count=0)

    tasks = await service.request_replace_classifier_work_rack(
        FakeDb(),
        operation_key="op-002",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER.value,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-002",
    )

    assert len(tasks) == 1
    assert tasks[0].sequence_no == 2
    assert tasks[0].task_type == WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value
    assert tasks[0].target_position_code == "CLASSIFIER-WORK"


@pytest.mark.asyncio
async def test_same_operation_move_out_releases_capacity_for_supply() -> None:
    service, _repo, _lifecycle, _placements = _service(active_placements=[_active_rack()], capacity=1)

    tasks = await service.request_replace_classifier_work_rack(
        FakeDb(),
        operation_key="op-capacity-ok",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-capacity-ok",
    )

    assert [task.task_type for task in tasks] == [
        WorklineRackTaskType.MOVE_RACK.value,
        WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
    ]


@pytest.mark.asyncio
async def test_other_operation_move_out_does_not_release_capacity() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.REQUESTED,
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=1,
        capacity=1,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await service.request_replace_classifier_work_rack(
            FakeDb(),
            operation_key="op-capacity-blocked",
            workline=_workline(),
            session=_session(),
            work_position_code="CLASSIFIER-WORK",
            new_rack_kind=RackKind.SINGLE_LAYER,
            move_out_target_position_role="SMT_EMPTY_RACK_AREA",
            supply_target_code="WMS-RACK",
            trace_id="trace-capacity-blocked",
        )


@pytest.mark.asyncio
async def test_other_operation_active_supply_occupies_target_capacity() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.REQUESTED,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        rack_kind=RackKind.SINGLE_LAYER.value,
        actions_json={"required": True},
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        capacity=1,
        task_repository=task_repository,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await service.request_replace_classifier_work_rack(
            db,
            operation_key="op-capacity-other-supply",
            workline=_workline(),
            session=_session(),
            work_position_code="CLASSIFIER-WORK",
            new_rack_kind=RackKind.SINGLE_LAYER,
            move_out_target_position_role="SMT_EMPTY_RACK_AREA",
            supply_target_code="WMS-RACK",
            trace_id="trace-capacity-other-supply",
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_other_operation_active_move_rack_to_target_occupies_capacity() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.REQUESTED,
        source_position_code="OTHER-POSITION",
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        rack_code="RACK-INCOMING",
        rack_kind=RackKind.SINGLE_LAYER.value,
        actions_json={"required": True},
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        capacity=1,
        task_repository=task_repository,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await service.request_replace_classifier_work_rack(
            db,
            operation_key="op-capacity-other-move-in",
            workline=_workline(),
            session=_session(),
            work_position_code="CLASSIFIER-WORK",
            new_rack_kind=RackKind.SINGLE_LAYER,
            move_out_target_position_role="SMT_EMPTY_RACK_AREA",
            supply_target_code="WMS-RACK",
            trace_id="trace-capacity-other-move-in",
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_repeated_replace_operation_returns_existing_tasks_without_rechecking_current_active_rack() -> None:
    task_repository = FakeRackTaskRepository()
    existing_move = task_repository.add_existing(
        operation_key="op-repeat",
        sequence_no=1,
        task_type=WorklineRackTaskType.MOVE_RACK,
        rack_code="RACK-OLD",
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
        target_position_role="SMT_EMPTY_RACK_AREA",
    )
    existing_supply = task_repository.add_existing(
        operation_key="op-repeat",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_code=None,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )
    db = FakeDb()

    tasks = await service.request_replace_classifier_work_rack(
        db,
        operation_key="op-repeat",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-repeat",
    )

    assert tasks == [existing_move, existing_supply]
    assert lifecycle.calls == []
    assert db.added == []


@pytest.mark.asyncio
async def test_repeated_replace_operation_rejects_single_supply_with_wrong_sequence_no() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-shape-supply-sequence",
        sequence_no=1,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="sequence_no differs"):
        await service.request_replace_classifier_work_rack(
            FakeDb(),
            operation_key="op-shape-supply-sequence",
            workline=_workline(),
            session=_session(),
            work_position_code="CLASSIFIER-WORK",
            new_rack_kind=RackKind.SINGLE_LAYER,
            move_out_target_position_role="SMT_EMPTY_RACK_AREA",
            supply_target_code="WMS-RACK",
            trace_id="trace-shape",
        )


@pytest.mark.asyncio
async def test_repeated_replace_operation_rejects_target_code_mismatch() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-shape-target-code",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="target_code differs"):
        await service.request_replace_classifier_work_rack(
            FakeDb(),
            operation_key="op-shape-target-code",
            workline=_workline(),
            session=_session(),
            work_position_code="CLASSIFIER-WORK",
            new_rack_kind=RackKind.SINGLE_LAYER,
            move_out_target_position_role="SMT_EMPTY_RACK_AREA",
            supply_target_code="OTHER-WMS-RACK",
            trace_id="trace-shape",
        )


@pytest.mark.asyncio
async def test_repeated_replace_operation_rejects_move_out_rack_code_mismatch() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-shape-move-rack",
        sequence_no=1,
        task_type=WorklineRackTaskType.MOVE_RACK,
        rack_code="RACK-PERSISTED",
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
        target_position_role="SMT_EMPTY_RACK_AREA",
        target_code="WMS-RACK",
        request_json={
            "operation_key": "op-shape-move-rack",
            "operation_type": WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
            "sequence_no": 1,
            "task_type": WorklineRackTaskType.MOVE_RACK.value,
            "source_position_code": "CLASSIFIER-WORK",
            "target_position_code": None,
            "target_position_role": "SMT_EMPTY_RACK_AREA",
            "rack_kind": RackKind.SINGLE_LAYER.value,
            "rack_code": "RACK-OTHER",
        },
    )
    task_repository.add_existing(
        operation_key="op-shape-move-rack",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="rack_code differs"):
        await service.request_replace_classifier_work_rack(
            FakeDb(),
            operation_key="op-shape-move-rack",
            workline=_workline(),
            session=_session(),
            work_position_code="CLASSIFIER-WORK",
            new_rack_kind=RackKind.SINGLE_LAYER,
            move_out_target_position_role="SMT_EMPTY_RACK_AREA",
            supply_target_code="WMS-RACK",
            trace_id="trace-shape",
        )


@pytest.mark.asyncio
async def test_request_reuses_existing_outbox_when_task_is_missing() -> None:
    outbox_repository = FakeOutboxRepository()
    existing_outbox = outbox_repository.add_existing(
        WorklineOutbox(
            id=88,
            session_id=300,
            workline_id=45,
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            dispatch_key="rack-operation:op-existing-outbox:2:ALLOCATE_AND_MOVE_RACK",
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-existing-outbox",
                "operation_type": WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-existing-outbox",
                "dispatch_key": "rack-operation:op-existing-outbox:2:ALLOCATE_AND_MOVE_RACK",
                "actions": {"action": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=OutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        outbox_repository=outbox_repository,
    )
    db = FakeDb()

    tasks = await service.request_replace_classifier_work_rack(
        db,
        operation_key="op-existing-outbox",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-existing-outbox",
    )

    assert len(tasks) == 1
    assert lifecycle.calls[0]["outbox"] is existing_outbox
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_request_reuses_existing_outbox_after_integrity_error_on_concurrent_insert() -> None:
    outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-outbox-race:2:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = outbox_repository.add_existing(
        WorklineOutbox(
            id=89,
            session_id=300,
            workline_id=45,
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-outbox-race",
                "operation_type": WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-outbox-race",
                "dispatch_key": dispatch_key,
                "actions": {"action": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=OutboxStatus.NEW,
        )
    )
    outbox_repository.return_none_once_for.add(dispatch_key)
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        outbox_repository=outbox_repository,
    )
    db = FakeDb()
    db.fail_next_flush_with_integrity = True

    tasks = await service.request_replace_classifier_work_rack(
        db,
        operation_key="op-outbox-race",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-outbox-race",
    )

    assert len(tasks) == 1
    assert lifecycle.calls[0]["outbox"] is existing_outbox
    assert outbox_repository.calls == [dispatch_key, dispatch_key]
    assert db.nested_rollback_count == 1
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_request_reuses_existing_outbox_when_only_trace_id_differs() -> None:
    outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-existing-outbox-trace:2:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = outbox_repository.add_existing(
        WorklineOutbox(
            id=90,
            session_id=300,
            workline_id=45,
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-existing-outbox-trace",
                "operation_type": WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-old",
                "dispatch_key": dispatch_key,
                "actions": {"action": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=OutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        outbox_repository=outbox_repository,
    )

    tasks = await service.request_replace_classifier_work_rack(
        FakeDb(),
        operation_key="op-existing-outbox-trace",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-new",
    )

    assert len(tasks) == 1
    assert lifecycle.calls[0]["outbox"] is existing_outbox


@pytest.mark.asyncio
async def test_operation_request_does_not_dispatch_http_inside_db_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _repo, _lifecycle, _placements = _service(active_placements=[_active_rack()])
    db = FakeDb()

    def fail_sync_dispatch(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("rack operation service must not dispatch external side effects")

    async def fail_async_dispatch(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("rack operation service must not dispatch external side effects")

    monkeypatch.setattr(celery_app, "send_task", fail_sync_dispatch)
    monkeypatch.setattr(httpx, "request", fail_sync_dispatch)
    monkeypatch.setattr(httpx, "post", fail_sync_dispatch)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail_async_dispatch)

    await service.request_replace_classifier_work_rack(
        db,
        operation_key="op-no-dispatch",
        workline=_workline(),
        session=_session(),
        work_position_code="CLASSIFIER-WORK",
        new_rack_kind=RackKind.SINGLE_LAYER,
        move_out_target_position_role="SMT_EMPTY_RACK_AREA",
        supply_target_code="WMS-RACK",
        trace_id="trace-no-dispatch",
    )

    outboxes = [item for item in db.added if isinstance(item, WorklineOutbox)]
    assert [(outbox.dispatch_type, outbox.target_type, outbox.status) for outbox in outboxes] == [
        (DispatchType.EXTERNAL_HTTP, TargetType.HTTP_ENDPOINT, OutboxStatus.NEW),
        (DispatchType.EXTERNAL_HTTP, TargetType.HTTP_ENDPOINT, OutboxStatus.NEW),
    ]


@pytest.mark.asyncio
async def test_derive_operation_status_requires_all_required_tasks_succeeded() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-status",
        sequence_no=1,
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code="RACK-OLD",
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
    )
    task_repository.add_existing(
        operation_key="op-status",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.REQUESTED,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, placements = _service(task_repository=task_repository)

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-status")
        == WorklineRackOperationStatus.PENDING.value
    )

    task_repository.tasks[1].task_status = WorklineRackTaskStatus.SUCCEEDED
    placements.placements_by_position["CLASSIFIER-WORK"] = [_active_rack("RACK-NEW")]
    placements.placements_by_position["CLASSIFIER-WORK-SOURCE"] = []

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-status")
        == WorklineRackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_resource_projection_confirmation() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, placements = _service(task_repository=task_repository)

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection")
        == WorklineRackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [_active_rack("RACK-NEW")]

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection")
        == WorklineRackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_reconciles_when_projection_rack_kind_mismatches() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection-kind",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, _placements = _service(
        task_repository=task_repository,
        placements_by_position={"CLASSIFIER-WORK": [_active_rack("RACK-WRONG", rack_kind=RackKind.FIVE_LAYER.value)]},
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection-kind")
        == WorklineRackOperationStatus.RECONCILING.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_reconciles_when_move_out_rack_still_at_source_position() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-source-not-cleared",
        sequence_no=1,
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code="RACK-OLD",
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
    )
    task_repository.add_existing(
        operation_key="op-source-not-cleared",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code=None,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, _placements = _service(
        task_repository=task_repository,
        placements_by_position={"CLASSIFIER-WORK": [_active_rack("RACK-OLD")]},
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-source-not-cleared")
        == WorklineRackOperationStatus.RECONCILING.value
    )
