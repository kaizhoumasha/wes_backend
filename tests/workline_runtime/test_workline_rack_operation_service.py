from __future__ import annotations

from types import SimpleNamespace, TracebackType
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from src.app.resource.models import RackKind
from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType, WorklineOutbox
from src.app.workline.models.rack_task import WorklineRackTask, WorklineRackTaskStatus, WorklineRackTaskType
from src.app.workline.models.session import SessionStatus
from src.app.workline.services.rack_gateway import WmsRcsRackGateway
from src.app.workline.services.rack_operation_service import (
    WorklineRackOperationService,
    WorklineRackOperationStatus,
    WorklineRackTaskSpec,
)
from src.celery_app.app import celery_app

RACK_TRANSPORT_OPERATION_TYPE = "RACK_TRANSPORT"
CLASSIFIER_WORK_POSITION_CODE = "CLASSIFIER-WORK"
CLASSIFIER_WORK_POSITION_ROLE = "SMT_CLASSIFIER_SINGLE_RACK_WORK"
MOVE_OUT_TARGET_POSITION_ROLE = "SMT_EMPTY_RACK_AREA"
RACK_OPERATION_TARGET_CODE = "WMS-RACK"


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

    async def list_move_rack_source_claims(
        self,
        _db: Any,
        *,
        workline_code: str,
        source_position_code: str,
        rack_code: str,
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
        move_rack_types = {WorklineRackTaskType.MOVE_RACK, WorklineRackTaskType.MOVE_RACK.value}
        return [
            task
            for task in self.tasks
            if task.workline_code == workline_code
            and task.source_position_code == source_position_code
            and task.rack_code == rack_code
            and task.task_type in move_rack_types
            and task.task_status in active_statuses
        ]

    def add_existing(self, **overrides: Any) -> SimpleNamespace:
        provided_keys = set(overrides)
        values = {
            "id": len(self.tasks) + 1,
            "task_key": f"task-{len(self.tasks) + 1}",
            "dispatch_key": f"dispatch-{len(self.tasks) + 1}",
            "operation_key": "op-001",
            "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
            "sequence_no": len(self.tasks) + 1,
            "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
            "task_status": WorklineRackTaskStatus.REQUESTED,
            "workline_code": "WL-SMT-01",
            "rack_code": None,
            "rack_kind": RackKind.SINGLE_LAYER.value,
            "source_position_code": None,
            "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
            "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            "target_code": RACK_OPERATION_TARGET_CODE,
            "actions_json": {},
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
        if "actions_json" not in provided_keys:
            values["actions_json"] = _expected_rack_task_envelope(values)["actions_json"]
        if "request_json" not in provided_keys:
            values["request_json"] = _expected_rack_task_envelope(values)["request_json"]
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
    def __init__(self, *, capacity: int = 1, allowed_rack_kind: RackKind = RackKind.SINGLE_LAYER) -> None:
        self.capacity = capacity
        self.allowed_rack_kind = allowed_rack_kind
        self.calls: list[dict[str, Any]] = []

    async def require_position_capacity_for_update(self, _db: Any, **kwargs: Any) -> tuple[SimpleNamespace, int]:
        self.calls.append(kwargs)
        if kwargs["rack_kind"] != self.allowed_rack_kind:
            raise ValueError(
                f"allowed rack kind mismatch: expected {self.allowed_rack_kind}, got {kwargs['rack_kind']}"
            )
        return (
            SimpleNamespace(
                position_code=kwargs["position_code"],
                position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
                allowed_rack_kind=self.allowed_rack_kind,
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
    allowed_rack_kind: RackKind = RackKind.SINGLE_LAYER,
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
            rack_position_service=FakeRackPositionService(capacity=capacity, allowed_rack_kind=allowed_rack_kind),
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
        current_wait_type=None,
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


def _expected_rack_task_envelope(values: dict[str, Any]) -> dict[str, Any]:
    return WmsRcsRackGateway().build_rack_task_envelope(
        operation_key=values["operation_key"],
        operation_type=values["operation_type"],
        workline_code=values["workline_code"],
        trace_id=values.get("trace_id", "trace-existing"),
        target_code=values["target_code"],
        spec=SimpleNamespace(
            sequence_no=values["sequence_no"],
            task_type=values["task_type"].value,
            rack_code=values["rack_code"],
            rack_kind=values["rack_kind"],
            source_position_code=values["source_position_code"],
            target_position_code=values["target_position_code"],
            target_position_role=values["target_position_role"],
            required=values.get("required", True),
        ),
    )


def test_move_rack_source_claim_is_database_unique() -> None:
    index = next(
        (
            table_index
            for table_index in WorklineRackTask.__table__.indexes
            if table_index.name == "ux_workline_rack_tasks_move_source_claim"
        ),
        None,
    )

    assert index is not None
    assert index.unique is True
    assert [column.name for column in index.columns] == ["workline_code", "source_position_code", "rack_code"]

    postgresql_where = str(index.dialect_options["postgresql"]["where"])
    assert "task_type = 'MOVE_RACK'" in postgresql_where
    assert "task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')" in postgresql_where
    assert "SUCCEEDED" not in postgresql_where
    assert "workline_code IS NOT NULL" in postgresql_where
    assert "source_position_code IS NOT NULL" in postgresql_where
    assert "rack_code IS NOT NULL" in postgresql_where


def _classifier_replacement_task_specs(
    *,
    include_move_out: bool = True,
    rack_code: str = "RACK-OLD",
    rack_kind: str = RackKind.SINGLE_LAYER.value,
    work_position_code: str = CLASSIFIER_WORK_POSITION_CODE,
    move_out_target_position_role: str = MOVE_OUT_TARGET_POSITION_ROLE,
    supply_target_position_role: str = CLASSIFIER_WORK_POSITION_ROLE,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if include_move_out:
        specs.append(
            {
                "sequence_no": 1,
                "task_type": WorklineRackTaskType.MOVE_RACK.value,
                "rack_code": rack_code,
                "rack_kind": rack_kind,
                "source_position_code": work_position_code,
                "target_position_role": move_out_target_position_role,
            }
        )
    specs.append(
        {
            "sequence_no": 2,
            "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "rack_kind": rack_kind,
            "target_position_code": work_position_code,
            "target_position_role": supply_target_position_role,
        }
    )
    return specs


async def _request_classifier_replacement(
    service: WorklineRackOperationService,
    db: Any,
    *,
    operation_key: str,
    session: Any | None = None,
    trace_id: str = "trace-001",
    include_move_out: bool = True,
    work_position_code: str = CLASSIFIER_WORK_POSITION_CODE,
    rack_kind: str = RackKind.SINGLE_LAYER.value,
    move_out_target_position_role: str = MOVE_OUT_TARGET_POSITION_ROLE,
    target_code: str = RACK_OPERATION_TARGET_CODE,
) -> list[Any]:
    return await service.request_operation_tasks(
        db,
        operation_key=operation_key,
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=_session() if session is None else session,
        target_code=target_code,
        trace_id=trace_id,
        task_specs=_classifier_replacement_task_specs(
            include_move_out=include_move_out,
            rack_kind=rack_kind,
            work_position_code=work_position_code,
            move_out_target_position_role=move_out_target_position_role,
        ),
    )


@pytest.mark.asyncio
async def test_request_operation_tasks_creates_plugin_defined_tasks_without_mutating_session() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[_active_rack()], capacity=1)
    db = FakeDb()
    session = _session()

    tasks = await service.request_operation_tasks(
        db,
        operation_key="op-plugin-owned",
        operation_type="RACK_TRANSPORT",
        workline=_workline(),
        session=session,
        target_code="WMS-RACK",
        trace_id="trace-plugin-owned",
        task_specs=[
            {
                "sequence_no": 1,
                "task_type": WorklineRackTaskType.MOVE_RACK.value,
                "rack_code": "RACK-OLD",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_EMPTY_RACK_AREA",
            },
            {
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
            },
        ],
    )

    assert [task.sequence_no for task in tasks] == [1, 2]
    assert [task.task_type for task in tasks] == [
        WorklineRackTaskType.MOVE_RACK.value,
        WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
    ]
    assert [call["operation_type"] for call in lifecycle.calls] == ["RACK_TRANSPORT", "RACK_TRANSPORT"]
    assert [call["dispatch_key"] for call in lifecycle.calls] == [
        "rack-operation:op-plugin-owned:1:MOVE_RACK",
        "rack-operation:op-plugin-owned:2:ALLOCATE_AND_MOVE_RACK",
    ]
    assert session.status == SessionStatus.RUNNING
    assert session.current_wait_type is None
    assert session.context_json == {"kept": "value"}
    assert session.awaiting_command_id == 99
    assert session.ended_at == "ended"
    assert session.failure_domain == "PLUGIN"


@pytest.mark.asyncio
async def test_request_operation_tasks_normalizes_dataclass_task_specs_through_gateway() -> None:
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "SOURCE-POSITION": [
                _active_rack(
                    rack_code="RACK-OPTIONAL",
                    rack_kind=RackKind.SINGLE_LAYER.value,
                )
            ],
            "TARGET-POSITION": [],
        },
        capacity=2,
    )
    db = FakeDb()

    tasks = await service.request_operation_tasks(
        db,
        operation_key="op-dataclass-spec",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=_session(),
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-dataclass-spec",
        task_specs=[
            WorklineRackTaskSpec(
                sequence_no=1,
                task_type=WorklineRackTaskType.MOVE_RACK.value,
                rack_code="RACK-OPTIONAL",
                rack_kind=RackKind.SINGLE_LAYER.value,
                source_position_code="SOURCE-POSITION",
                target_position_code="TARGET-POSITION",
                target_position_role=CLASSIFIER_WORK_POSITION_ROLE,
                required=False,
            )
        ],
    )

    assert [task.sequence_no for task in tasks] == [1]
    call = lifecycle.calls[0]
    assert call["dispatch_key"] == "rack-operation:op-dataclass-spec:1:MOVE_RACK"
    assert call["target_code"] == RACK_OPERATION_TARGET_CODE
    assert call["actions_json"]["action"] == WorklineRackTaskType.MOVE_RACK.value
    assert call["actions_json"]["required"] is False
    assert call["request_json"]["callback_type"] == "WMS_RACK_MOVED"
    assert call["request_json"]["operation_key"] == "op-dataclass-spec"
    assert call["request_json"]["operation_type"] == RACK_TRANSPORT_OPERATION_TYPE
    assert call["request_json"]["workline_code"] == "WL-SMT-01"
    assert call["request_json"]["trace_id"] == "trace-dataclass-spec"


@pytest.mark.asyncio
async def test_request_operation_tasks_creates_move_out_and_supply_tasks_with_same_operation_key() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[_active_rack()])
    db = FakeDb()
    session = _session()

    tasks = await _request_classifier_replacement(
        service,
        db,
        operation_key="op-001",
        session=session,
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
    assert session.status == SessionStatus.RUNNING
    assert session.context_json == {"kept": "value"}
    assert session.awaiting_command_id == 99


@pytest.mark.asyncio
async def test_request_operation_tasks_without_move_out_creates_only_supply_task() -> None:
    service, _repo, _lifecycle, _placements = _service(active_placements=[], active_count=0)

    tasks = await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key="op-002",
        trace_id="trace-002",
        include_move_out=False,
    )

    assert len(tasks) == 1
    assert tasks[0].sequence_no == 2
    assert tasks[0].task_type == WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value
    assert tasks[0].target_position_code == "CLASSIFIER-WORK"


@pytest.mark.asyncio
async def test_allocate_and_move_rack_requires_target_position_code_before_dispatch() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[], active_count=0)
    db = FakeDb()

    with pytest.raises(ValueError, match="ALLOCATE_AND_MOVE_RACK requires target_position_code"):
        await service.request_operation_tasks(
            db,
            operation_key="op-missing-supply-target",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-missing-supply-target",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_move_rack_requires_destination_before_dispatch() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[_active_rack("RACK-OLD")])
    db = FakeDb()

    with pytest.raises(ValueError, match="MOVE_RACK requires target_position_code or target_position_role"):
        await service.request_operation_tasks(
            db,
            operation_key="op-missing-move-destination",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-missing-move-destination",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-OLD",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_same_operation_move_out_releases_capacity_for_supply() -> None:
    service, _repo, _lifecycle, _placements = _service(active_placements=[_active_rack()], capacity=1)

    tasks = await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key="op-capacity-ok",
        trace_id="trace-capacity-ok",
    )

    assert [task.task_type for task in tasks] == [
        WorklineRackTaskType.MOVE_RACK.value,
        WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
    ]


@pytest.mark.asyncio
async def test_same_operation_move_out_must_match_active_source_rack_to_release_capacity() -> None:
    service, _repo, lifecycle, _placements = _service(
        active_placements=[_active_rack("RACK-ACTUAL")],
        capacity=1,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="MOVE_RACK source rack mismatch"):
        await _request_classifier_replacement(
            service,
            db,
            operation_key="op-stale-move-out",
            trace_id="trace-stale-move-out",
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_non_required_supply_still_requires_target_capacity() -> None:
    service, _repo, lifecycle, _placements = _service(
        active_placements=[_active_rack("RACK-EXISTING")],
        capacity=1,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await service.request_operation_tasks(
            db,
            operation_key="op-non-required-supply",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-non-required-supply",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                    "required": False,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_non_required_move_rack_to_target_still_requires_target_capacity() -> None:
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "OTHER-POSITION": [_active_rack("RACK-INCOMING")],
            CLASSIFIER_WORK_POSITION_CODE: [_active_rack("RACK-EXISTING")],
        },
        capacity=1,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await service.request_operation_tasks(
            db,
            operation_key="op-non-required-move-in",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-non-required-move-in",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-INCOMING",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "OTHER-POSITION",
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                    "required": False,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_move_rack_source_must_match_resource_projection_before_dispatch() -> None:
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "SOURCE-POSITION": [],
            "TARGET-POSITION": [],
        },
        capacity=2,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="MOVE_RACK source rack mismatch"):
        await service.request_operation_tasks(
            db,
            operation_key="op-stale-move-source",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-stale-move-source",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-STALE",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_code": "TARGET-POSITION",
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_move_rack_source_rack_kind_must_match_resource_projection_before_dispatch() -> None:
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "SOURCE-POSITION": [_active_rack("RACK-FIVE", rack_kind=RackKind.FIVE_LAYER.value)],
            "TARGET-POSITION": [],
        },
        capacity=2,
        allowed_rack_kind=RackKind.SINGLE_LAYER,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="MOVE_RACK source rack_kind mismatch"):
        await service.request_operation_tasks(
            db,
            operation_key="op-source-kind-mismatch",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-source-kind-mismatch",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-FIVE",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_code": "TARGET-POSITION",
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_move_rack_source_rack_must_be_unique_within_request() -> None:
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "SOURCE-POSITION": [_active_rack("RACK-DUP")],
        },
        capacity=2,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="MOVE_RACK source rack duplicated"):
        await service.request_operation_tasks(
            db,
            operation_key="op-duplicate-source-rack",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-duplicate-source-rack",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-DUP",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                },
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-DUP",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_move_rack_source_rack_must_not_have_active_claim() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.REQUESTED,
        rack_code="RACK-CLAIMED",
        source_position_code="SOURCE-POSITION",
        target_position_code=None,
    )
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "SOURCE-POSITION": [_active_rack("RACK-CLAIMED")],
        },
        capacity=2,
        task_repository=task_repository,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="MOVE_RACK source rack already claimed"):
        await service.request_operation_tasks(
            db,
            operation_key="op-source-rack-claimed",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-source-rack-claimed",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-CLAIMED",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_historical_succeeded_move_rack_does_not_keep_source_rack_claimed() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code="RACK-RECONCILING",
        source_position_code="SOURCE-POSITION",
        target_position_code=None,
    )
    service, _repo, lifecycle, _placements = _service(
        placements_by_position={
            "SOURCE-POSITION": [_active_rack("RACK-RECONCILING")],
        },
        capacity=2,
        task_repository=task_repository,
    )
    db = FakeDb()

    tasks = await service.request_operation_tasks(
        db,
        operation_key="op-source-rack-reused",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=_session(),
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-source-rack-reused",
        task_specs=[
            {
                "sequence_no": 1,
                "task_type": WorklineRackTaskType.MOVE_RACK.value,
                "rack_code": "RACK-RECONCILING",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": "SOURCE-POSITION",
                "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
            }
        ],
    )

    assert [task.operation_key for task in tasks] == ["op-source-rack-reused"]
    assert len(lifecycle.calls) == 1


@pytest.mark.asyncio
async def test_every_inbound_task_requires_rack_kind() -> None:
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        capacity=2,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="requires rack_kind"):
        await service.request_operation_tasks(
            db,
            operation_key="op-missing-inbound-kind",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-missing-inbound-kind",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_every_inbound_task_rack_kind_must_be_allowed_by_position() -> None:
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        capacity=2,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="allowed rack kind mismatch"):
        await service.request_operation_tasks(
            db,
            operation_key="op-mismatched-inbound-kind",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-mismatched-inbound-kind",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.FIVE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_non_required_same_operation_move_out_does_not_release_required_capacity() -> None:
    service, _repo, lifecycle, _placements = _service(
        active_placements=[_active_rack("RACK-OLD")],
        capacity=1,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await service.request_operation_tasks(
            db,
            operation_key="op-non-required-move-out-release",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-non-required-move-out-release",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-OLD",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                    "required": False,
                },
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


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
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key="op-capacity-blocked",
            trace_id="trace-capacity-blocked",
            include_move_out=False,
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
        await _request_classifier_replacement(
            service,
            db,
            operation_key="op-capacity-other-supply",
            trace_id="trace-capacity-other-supply",
            include_move_out=False,
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
        await _request_classifier_replacement(
            service,
            db,
            operation_key="op-capacity-other-move-in",
            trace_id="trace-capacity-other-move-in",
            include_move_out=False,
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


@pytest.mark.asyncio
async def test_other_operation_non_required_move_rack_to_target_occupies_capacity() -> None:
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
        actions_json={"required": False},
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        capacity=1,
        task_repository=task_repository,
    )
    db = FakeDb()

    with pytest.raises(ValueError, match="rack operation target position capacity unavailable"):
        await _request_classifier_replacement(
            service,
            db,
            operation_key="op-capacity-other-non-required-move-in",
            trace_id="trace-capacity-other-non-required-move-in",
            include_move_out=False,
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

    tasks = await _request_classifier_replacement(
        service,
        db,
        operation_key="op-repeat",
        trace_id="trace-repeat",
    )

    assert tasks == [existing_move, existing_supply]
    assert lifecycle.calls == []
    assert db.added == []


@pytest.mark.asyncio
async def test_repeated_operation_rejects_missing_non_required_physical_task() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-repeat-missing-optional",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
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

    with pytest.raises(ValueError, match="task count differs"):
        await service.request_operation_tasks(
            db,
            operation_key="op-repeat-missing-optional",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-repeat-missing-optional",
            task_specs=[
                {
                    "sequence_no": 1,
                    "task_type": WorklineRackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-OLD",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                    "required": False,
                },
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, WorklineOutbox)] == []


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

    with pytest.raises(ValueError, match="task identity differs"):
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key="op-shape-supply-sequence",
            trace_id="trace-shape",
            include_move_out=False,
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

    with pytest.raises(ValueError, match="task identity differs"):
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key="op-shape-target-code",
            trace_id="trace-shape",
            include_move_out=False,
            target_code="OTHER-WMS-RACK",
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
            "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
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

    with pytest.raises(ValueError, match="request_json rack_code differs"):
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key="op-shape-move-rack",
            trace_id="trace-shape",
        )


@pytest.mark.asyncio
async def test_request_operation_rejects_plugin_owned_request_json() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-request-json-drift",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
        request_json={
            "operation_key": "op-request-json-drift",
            "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
            "sequence_no": 2,
            "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "workline_code": "WL-SMT-01",
            "source_position_code": None,
            "target_position_code": "CLASSIFIER-WORK",
            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
            "rack_kind": RackKind.SINGLE_LAYER.value,
            "rack_code": None,
            "route_profile": "OLD",
        },
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="插件不得传入货架外部派发字段: request_json"):
        await service.request_operation_tasks(
            FakeDb(),
            operation_key="op-request-json-drift",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-request-json-drift",
            task_specs=[
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                    "request_json": {"route_profile": "NEW"},
                }
            ],
        )


@pytest.mark.asyncio
async def test_request_operation_rejects_plugin_owned_actions_json() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-actions-json-drift",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
        actions_json={
            "action": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "required": True,
            "route_profile": "OLD",
        },
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="插件不得传入货架外部派发字段: actions_json"):
        await service.request_operation_tasks(
            FakeDb(),
            operation_key="op-actions-json-drift",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-actions-json-drift",
            task_specs=[
                {
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                    "actions_json": {"route_profile": "NEW"},
                }
            ],
        )


@pytest.mark.asyncio
async def test_repeated_operation_allows_persisted_timeout_evidence() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-timeout-evidence",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
        request_json={
            **_expected_rack_task_envelope(
                {
                    "operation_key": "op-timeout-evidence",
                    "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                    "sequence_no": 2,
                    "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
                    "workline_code": "WL-SMT-01",
                    "rack_code": None,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": None,
                    "target_position_code": "CLASSIFIER-WORK",
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                    "target_code": "WMS-RACK",
                }
            )["request_json"],
            "timeout_seconds": 300,
        },
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    tasks = await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key="op-timeout-evidence",
        trace_id="trace-timeout-evidence-retry",
        include_move_out=False,
    )

    assert len(tasks) == 1
    assert tasks[0].operation_key == "op-timeout-evidence"


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
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-existing-outbox",
                "request_id": "rack-operation:op-existing-outbox:2:ALLOCATE_AND_MOVE_RACK",
                "callback_type": "WMS_RACK_ARRIVED",
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

    tasks = await _request_classifier_replacement(
        service,
        db,
        operation_key="op-existing-outbox",
        trace_id="trace-existing-outbox",
        include_move_out=False,
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
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-outbox-race",
                "request_id": dispatch_key,
                "callback_type": "WMS_RACK_ARRIVED",
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

    tasks = await _request_classifier_replacement(
        service,
        db,
        operation_key="op-outbox-race",
        trace_id="trace-outbox-race",
        include_move_out=False,
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
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-old",
                "request_id": dispatch_key,
                "callback_type": "WMS_RACK_ARRIVED",
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

    tasks = await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key="op-existing-outbox-trace",
        trace_id="trace-new",
        include_move_out=False,
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

    await _request_classifier_replacement(
        service,
        db,
        operation_key="op-no-dispatch",
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
async def test_derive_operation_status_consumes_projection_per_inbound_task() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection-count",
        sequence_no=1,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code=None,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    task_repository.add_existing(
        operation_key="op-projection-count",
        sequence_no=2,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code=None,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, placements = _service(
        task_repository=task_repository,
        placements_by_position={"CLASSIFIER-WORK": [_active_rack("RACK-NEW-1")]},
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection-count")
        == WorklineRackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [
        _active_rack("RACK-NEW-1"),
        _active_rack("RACK-NEW-2"),
    ]

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection-count")
        == WorklineRackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_move_rack_target_projection_confirmation() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-move-target",
        sequence_no=1,
        task_type=WorklineRackTaskType.MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        rack_code="RACK-MOVED",
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code="SOURCE-POSITION",
        target_position_code="TARGET-POSITION",
    )
    service, _repo, _lifecycle, placements = _service(
        task_repository=task_repository,
        placements_by_position={
            "SOURCE-POSITION": [],
            "TARGET-POSITION": [],
        },
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-move-target")
        == WorklineRackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["TARGET-POSITION"] = [_active_rack("RACK-MOVED")]

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-move-target")
        == WorklineRackOperationStatus.SUCCEEDED.value
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
