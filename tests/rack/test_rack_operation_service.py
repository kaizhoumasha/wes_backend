from __future__ import annotations

from types import SimpleNamespace, TracebackType
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from src.app.rack.models import RackOperationBase, RackOperationStatus, RackTask, RackTaskStatus, RackTaskType
from src.app.rack.services import (
    RackOperationService,
    RackTaskSpec,
)
from src.app.rack.services.gateway import WmsRcsRackGateway
from src.app.resource.models import RackKind
from src.app.sys.models import (
    OperationCompletionPolicy,
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.workline.models.session import SessionStatus
from src.celery_app.app import celery_app
from src.utils.timezone import timezone

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
            raise IntegrityError("INSERT INTO system_outbox", {}, Exception("duplicate dispatch_key"))
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
            RackTaskStatus.PLANNED,
            RackTaskStatus.REQUESTED,
            RackTaskStatus.IN_PROGRESS,
            RackTaskStatus.RECONCILING,
            RackTaskStatus.PLANNED.value,
            RackTaskStatus.REQUESTED.value,
            RackTaskStatus.IN_PROGRESS.value,
            RackTaskStatus.RECONCILING.value,
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
            RackTaskStatus.PLANNED,
            RackTaskStatus.REQUESTED,
            RackTaskStatus.IN_PROGRESS,
            RackTaskStatus.RECONCILING,
            RackTaskStatus.PLANNED.value,
            RackTaskStatus.REQUESTED.value,
            RackTaskStatus.IN_PROGRESS.value,
            RackTaskStatus.RECONCILING.value,
        }
        move_rack_types = {RackTaskType.MOVE_RACK, RackTaskType.MOVE_RACK.value}
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
            "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK,
            "task_status": RackTaskStatus.REQUESTED,
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
            values["actions_json"] = {"action": values["task_type"].value, "required": True}
        if "request_json" not in provided_keys:
            values["request_json"] = {
                "operation_key": values["operation_key"],
                "operation_type": values["operation_type"],
                "sequence_no": values["sequence_no"],
                "task_type": values["task_type"].value,
                "workline_code": values["workline_code"],
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
            task_status=RackTaskStatus.REQUESTED,
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
        task.workline_code = getattr(kwargs["workline"], "line_code", None)
        task.material_session_id = getattr(kwargs["session"], "id", None)
        self.repository.tasks.append(task)
        return task


class FakeRackOperationRepository:
    def __init__(
        self,
        *,
        completion_policy: OperationCompletionPolicy = OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION,
        existing: bool = True,
    ) -> None:
        self.operation = (
            SimpleNamespace(
                id=1,
                operation_key="op-001",
                operation_type=RACK_TRANSPORT_OPERATION_TYPE,
                completion_policy=completion_policy,
            )
            if existing
            else None
        )

    async def get_by_operation_key(self, _db: Any, operation_key: str) -> SimpleNamespace | None:
        if self.operation is not None and operation_key == self.operation.operation_key:
            return self.operation
        return None

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        self.operation = SimpleNamespace(id=1, **data)
        return self.operation

    async def mark_status(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.operation.operation_status = kwargs["operation_status"]
        self.operation.result_json = {
            **getattr(self.operation, "result_json", {}),
            **kwargs.get("result_json_patch", {}),
        }
        return self.operation


class DuplicateOnceRackOperationRepository(FakeRackOperationRepository):
    def __init__(self) -> None:
        super().__init__(completion_policy=OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION)
        self.operation.operation_key = "op-concurrent"
        self.operation.operation_type = RACK_TRANSPORT_OPERATION_TYPE
        self.get_calls = 0
        self.create_calls = 0

    async def get_by_operation_key(self, _db: Any, operation_key: str) -> SimpleNamespace | None:
        self.get_calls += 1
        if self.get_calls == 1:
            return None
        if operation_key == self.operation.operation_key:
            return self.operation
        return None

    async def create(self, _db: Any, data: dict[str, Any]) -> SimpleNamespace:
        self.create_calls += 1
        raise IntegrityError("INSERT INTO rack_operations", {}, Exception("duplicate operation_key"))


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.by_dispatch_key: dict[str, SystemOutbox] = {}
        self.calls: list[str] = []
        self.locked_calls: list[str] = []
        self.return_none_once_for: set[str] = set()

    async def get_by_dispatch_key(self, _db: Any, dispatch_key: str) -> SystemOutbox | None:
        self.calls.append(dispatch_key)
        if dispatch_key in self.return_none_once_for:
            self.return_none_once_for.remove(dispatch_key)
            return None
        return self.by_dispatch_key.get(dispatch_key)

    async def get_by_dispatch_key_for_update(self, _db: Any, dispatch_key: str) -> SystemOutbox | None:
        self.locked_calls.append(dispatch_key)
        if dispatch_key in self.return_none_once_for:
            self.return_none_once_for.remove(dispatch_key)
            return None
        return self.by_dispatch_key.get(dispatch_key)

    def add_existing(self, outbox: SystemOutbox) -> SystemOutbox:
        self.by_dispatch_key[outbox.dispatch_key] = outbox
        return outbox


class FakeStationLeaseService:
    def __init__(
        self,
        *,
        unavailable_positions: set[str] | None = None,
        active_rack_bound_positions: set[str] | None = None,
    ) -> None:
        self.unavailable_positions = unavailable_positions or set()
        self.active_rack_bound_positions = active_rack_bound_positions or set()
        self.calls: list[dict[str, Any]] = []

    async def claim_station_dispatch_lease(self, db: Any, **kwargs: Any) -> SystemOutbox | None:
        self.calls.append(kwargs)
        if kwargs["position_code"] in self.unavailable_positions:
            return None
        if kwargs["position_code"] in self.active_rack_bound_positions and not kwargs["allow_active_rack_bound"]:
            return None

        envelope = kwargs["envelope"]
        payload_json = dict(envelope.payload_json)
        station = dict(payload_json.get("station") or {})
        station.setdefault("workline_code", kwargs["workline_code"])
        station["position_code"] = kwargs["position_code"]
        payload_json["station"] = station
        payload_json.setdefault("workline_code", kwargs["workline_code"])
        payload_json["position_code"] = kwargs["position_code"]
        outbox = SystemOutbox(
            session_id=envelope.session_id,
            workline_id=kwargs["workline_id"],
            operation_domain=envelope.operation_domain,
            operation_key=envelope.operation_key,
            dispatch_type=envelope.dispatch_type,
            dispatch_key=envelope.dispatch_key,
            target_type=envelope.target_type,
            target_code=envelope.target_code,
            payload_json=payload_json,
            status=SystemOutboxStatus.NEW,
            trace_id=envelope.trace_id,
        )
        db.add(outbox)
        await db.flush()
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
    system_outbox_repository: FakeOutboxRepository | None = None,
    rack_operation_repository: Any | None = None,
    station_lease_service: FakeStationLeaseService | None = None,
) -> tuple[
    RackOperationService,
    FakeRackTaskRepository,
    FakeRackTaskLifecycleService,
    FakeRackPlacementRepository,
]:
    task_repository = task_repository or FakeRackTaskRepository()
    system_outbox_repository = system_outbox_repository or FakeOutboxRepository()
    rack_operation_repository = rack_operation_repository or FakeRackOperationRepository(existing=False)
    station_lease_service = station_lease_service or FakeStationLeaseService()
    lifecycle_service = FakeRackTaskLifecycleService(task_repository)
    placement_repository = FakeRackPlacementRepository(
        active_placements=active_placements,
        active_count=active_count,
        placements_by_position=placements_by_position,
    )
    return (
        RackOperationService(
            rack_operation_repository=rack_operation_repository,
            rack_task_repository=task_repository,
            rack_task_lifecycle_service=lifecycle_service,
            outbox_repository=system_outbox_repository,
            rack_position_service=FakeRackPositionService(capacity=capacity, allowed_rack_kind=allowed_rack_kind),
            rack_placement_repository=placement_repository,
            station_lease_service=station_lease_service,
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


def test_rack_gateway_uses_supported_lifecycle_callback_types_for_rack_tasks() -> None:
    gateway = WmsRcsRackGateway()

    callback_types = {
        task_type: gateway.build_task_envelope(
            operation_key=f"op-{task_type}",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            sequence_no=1,
            task_type=task_type,
            workline_code="WL-SMT-01",
            workline_id=45,
            material_session_id=300,
            trace_id=f"trace-{task_type}",
            target_code=RACK_OPERATION_TARGET_CODE,
            rack_code="RACK-001",
            rack_kind=RackKind.SINGLE_LAYER.value,
            source_position_code="SOURCE-POSITION",
            target_position_code="TARGET-POSITION",
            target_position_role=CLASSIFIER_WORK_POSITION_ROLE,
        ).payload_json["callback_type"]
        for task_type in (
            RackTaskType.MOVE_RACK.value,
            RackTaskType.TURN_RACK_SIDE.value,
            RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
        )
    }

    assert callback_types == {
        RackTaskType.MOVE_RACK.value: "WMS_RACK_TASK_RESULT",
        RackTaskType.TURN_RACK_SIDE.value: "WMS_RACK_TASK_RESULT",
        RackTaskType.ALLOCATE_AND_MOVE_RACK.value: "WMS_RACK_ARRIVED",
    }


def test_move_rack_source_claim_is_database_unique() -> None:
    index = next(
        (
            table_index
            for table_index in RackTask.__table__.indexes
            if table_index.name == "ux_rack_tasks_move_source_claim"
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
                "task_type": RackTaskType.MOVE_RACK.value,
                "rack_code": rack_code,
                "rack_kind": rack_kind,
                "source_position_code": work_position_code,
                "target_position_role": move_out_target_position_role,
            }
        )
    specs.append(
        {
            "sequence_no": 2,
            "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "rack_kind": rack_kind,
            "target_position_code": work_position_code,
            "target_position_role": supply_target_position_role,
        }
    )
    return specs


async def _request_classifier_replacement(
    service: RackOperationService,
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
                "task_type": RackTaskType.MOVE_RACK.value,
                "rack_code": "RACK-OLD",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_EMPTY_RACK_AREA",
            },
            {
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
            },
        ],
    )

    assert [task.sequence_no for task in tasks] == [1, 2]
    assert [task.task_type for task in tasks] == [
        RackTaskType.MOVE_RACK.value,
        RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
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
async def test_request_operation_tasks_reloads_operation_after_concurrent_unique_conflict() -> None:
    operation_repository = DuplicateOnceRackOperationRepository()
    service, _repo, lifecycle, _placements = _service(
        active_placements=[_active_rack()],
        capacity=1,
        rack_operation_repository=operation_repository,
    )

    tasks = await service.request_operation_tasks(
        FakeDb(),
        operation_key="op-concurrent",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=_session(),
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-concurrent",
        task_specs=_classifier_replacement_task_specs(),
    )

    assert len(tasks) == 2
    assert operation_repository.create_calls == 1
    assert operation_repository.get_calls >= 2
    assert [call["operation_id"] for call in lifecycle.calls] == [operation_repository.operation.id] * 2


@pytest.mark.asyncio
async def test_request_operation_tasks_normalizes_dataclass_task_specs_before_dispatch() -> None:
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
            RackTaskSpec(
                sequence_no=1,
                task_type=RackTaskType.MOVE_RACK.value,
                rack_code="RACK-OPTIONAL",
                rack_kind=RackKind.SINGLE_LAYER.value,
                source_position_code="SOURCE-POSITION",
                target_position_code="TARGET-POSITION",
                target_position_role=CLASSIFIER_WORK_POSITION_ROLE,
                dispatch_key="caller-dispatch-key",
                target_code="CALLER-TARGET",
                request_json={"caller_field": "kept"},
                actions_json={},
                required=False,
            )
        ],
    )

    assert [task.sequence_no for task in tasks] == [1]
    call = lifecycle.calls[0]
    assert call["dispatch_key"] == "caller-dispatch-key"
    assert call["target_code"] == "CALLER-TARGET"
    assert call["actions_json"]["action"] == RackTaskType.MOVE_RACK.value
    assert call["actions_json"]["required"] is False
    assert call["request_json"]["caller_field"] == "kept"
    assert call["request_json"]["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert call["request_json"]["operation_key"] == "op-dataclass-spec"
    assert call["request_json"]["operation_type"] == RACK_TRANSPORT_OPERATION_TYPE
    assert call["request_json"]["workline_code"] == "WL-SMT-01"
    assert call["request_json"]["trace_id"] == "trace-dataclass-spec"


async def test_request_operation_tasks_preserves_material_context_in_external_outbox_payload() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[], active_count=0)
    db = FakeDb()

    await service.request_operation_tasks(
        db,
        operation_key="op-large-material",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=_session(),
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-large-material",
        task_specs=[
            {
                "sequence_no": 1,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                "request_json": {
                    "material": {
                        "HHPN": "IC001",
                        "LotCode": "LOT-I",
                        "DateCode": "20260413",
                        "PkgID": "PKG-IC001-LOT-I-001",
                        "reel_diameter": "330.0",
                        "reel_thickness": "24.0",
                    },
                },
            }
        ],
    )

    material = {
        "HHPN": "IC001",
        "LotCode": "LOT-I",
        "DateCode": "20260413",
        "PkgID": "PKG-IC001-LOT-I-001",
        "reel_diameter": "330.0",
        "reel_thickness": "24.0",
    }
    assert lifecycle.calls[0]["request_json"]["material"] == material
    outboxes = [item for item in db.added if isinstance(item, SystemOutbox)]
    assert outboxes[0].payload_json["material"] == material


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
        RackTaskType.MOVE_RACK.value,
        RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
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
    assert [item.status for item in db.added if isinstance(item, SystemOutbox)] == [
        SystemOutboxStatus.NEW,
        SystemOutboxStatus.NEW,
    ]
    assert session.status == SessionStatus.RUNNING
    assert session.context_json == {"kept": "value"}
    assert session.awaiting_command_id == 99


@pytest.mark.asyncio
async def test_request_operation_tasks_claims_station_dispatch_lease_for_single_layer_station_tasks() -> None:
    station_lease_service = FakeStationLeaseService()
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[_active_rack()],
        station_lease_service=station_lease_service,
    )

    await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key="op-station-lease",
        trace_id="trace-station-lease",
    )

    assert [call["position_code"] for call in station_lease_service.calls] == [
        CLASSIFIER_WORK_POSITION_CODE,
        CLASSIFIER_WORK_POSITION_CODE,
    ]
    assert [call["allow_active_rack_bound"] for call in station_lease_service.calls] == [True, True]
    assert {call["allow_active_operation_key"] for call in station_lease_service.calls} == {"op-station-lease"}
    assert [call["envelope"].dispatch_key for call in station_lease_service.calls] == [
        "rack-operation:op-station-lease:1:MOVE_RACK",
        "rack-operation:op-station-lease:2:ALLOCATE_AND_MOVE_RACK",
    ]


@pytest.mark.asyncio
async def test_request_operation_tasks_allows_same_operation_supply_to_replace_active_station_rack() -> None:
    station_lease_service = FakeStationLeaseService(active_rack_bound_positions={CLASSIFIER_WORK_POSITION_CODE})
    service, _repo, lifecycle, _placements = _service(
        active_placements=[_active_rack()],
        station_lease_service=station_lease_service,
    )

    tasks = await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key="op-replace-active-station",
        trace_id="trace-replace-active-station",
    )

    assert [task.task_type for task in tasks] == [
        RackTaskType.MOVE_RACK.value,
        RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
    ]
    assert [call["dispatch_key"] for call in lifecycle.calls] == [
        "rack-operation:op-replace-active-station:1:MOVE_RACK",
        "rack-operation:op-replace-active-station:2:ALLOCATE_AND_MOVE_RACK",
    ]
    assert [call["allow_active_rack_bound"] for call in station_lease_service.calls] == [True, True]


@pytest.mark.asyncio
async def test_request_operation_tasks_rejects_single_layer_task_when_station_dispatch_lease_busy() -> None:
    station_lease_service = FakeStationLeaseService(unavailable_positions={CLASSIFIER_WORK_POSITION_CODE})
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        station_lease_service=station_lease_service,
    )

    with pytest.raises(ValueError, match="station dispatch lease is not available"):
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key="op-station-busy",
            trace_id="trace-station-busy",
            include_move_out=False,
        )

    assert lifecycle.calls == []
    assert [call["position_code"] for call in station_lease_service.calls] == [CLASSIFIER_WORK_POSITION_CODE]


@pytest.mark.asyncio
async def test_request_operation_tasks_rejects_supply_only_when_active_station_rack_bound() -> None:
    station_lease_service = FakeStationLeaseService(active_rack_bound_positions={CLASSIFIER_WORK_POSITION_CODE})
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        station_lease_service=station_lease_service,
    )

    with pytest.raises(ValueError, match="station dispatch lease is not available"):
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key="op-supply-active-station",
            trace_id="trace-supply-active-station",
            include_move_out=False,
        )

    assert lifecycle.calls == []
    assert [call["allow_active_rack_bound"] for call in station_lease_service.calls] == [False]


@pytest.mark.asyncio
async def test_move_out_active_rack_action_aliases_to_move_rack_task() -> None:
    service, _repo, lifecycle, _placements = _service(active_placements=[_active_rack()], capacity=1)
    db = FakeDb()

    tasks = await service.request_operation_tasks(
        db,
        operation_key="op-move-out-active-alias",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=_session(),
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-move-out-active-alias",
        task_specs=[
            {
                "sequence_no": 1,
                "task_type": "MOVE_OUT_ACTIVE_RACK",
                "rack_code": "RACK-OLD",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
            },
            {
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            },
        ],
    )

    assert [task.task_type for task in tasks] == [
        RackTaskType.MOVE_RACK.value,
        RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
    ]
    assert lifecycle.calls[0]["actions_json"]["action"] == "MOVE_OUT_ACTIVE_RACK"
    assert lifecycle.calls[0]["actions_json"]["task_type"] == RackTaskType.MOVE_RACK.value
    assert lifecycle.calls[0]["request_json"]["task_type"] == RackTaskType.MOVE_RACK.value
    assert lifecycle.calls[0]["request_json"]["actions"]["action"] == "MOVE_OUT_ACTIVE_RACK"


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
    assert tasks[0].task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value
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
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-OLD",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
        RackTaskType.MOVE_RACK.value,
        RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
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
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                    "required": False,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.MOVE_RACK.value,
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
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-STALE",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_code": "TARGET-POSITION",
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-FIVE",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_code": "TARGET-POSITION",
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-DUP",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                },
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-DUP",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_move_rack_source_rack_must_not_have_active_claim() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-CLAIMED",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": "SOURCE-POSITION",
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_historical_succeeded_move_rack_does_not_keep_source_rack_claimed() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
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
                "task_type": RackTaskType.MOVE_RACK.value,
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
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.FIVE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-OLD",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                    "required": False,
                },
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_other_operation_move_out_does_not_release_capacity() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
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
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
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
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_other_operation_active_move_rack_to_target_occupies_capacity() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
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
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_other_operation_non_required_move_rack_to_target_occupies_capacity() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="other-op",
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
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
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_repeated_replace_operation_returns_existing_tasks_without_rechecking_current_active_rack() -> None:
    task_repository = FakeRackTaskRepository()
    existing_move = task_repository.add_existing(
        operation_key="op-repeat",
        sequence_no=1,
        task_type=RackTaskType.MOVE_RACK,
        rack_code="RACK-OLD",
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
        target_position_role="SMT_EMPTY_RACK_AREA",
    )
    existing_supply = task_repository.add_existing(
        operation_key="op-repeat",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
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
async def test_repeated_operation_links_existing_task_outbox_to_current_session() -> None:
    task_repository = FakeRackTaskRepository()
    existing_task = task_repository.add_existing(
        operation_key="op-repeated-ownerless",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code=CLASSIFIER_WORK_POSITION_CODE,
        target_position_role=CLASSIFIER_WORK_POSITION_ROLE,
        target_code=RACK_OPERATION_TARGET_CODE,
    )
    system_outbox_repository = FakeOutboxRepository()
    existing_outbox = system_outbox_repository.add_existing(
        SystemOutbox(
            dispatch_key=existing_task.dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=RACK_OPERATION_TARGET_CODE,
            operation_domain="RACK",
            operation_key="op-repeated-ownerless",
            workline_id=45,
            session_id=None,
            payload_json=dict(existing_task.request_json),
            status=SystemOutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        task_repository=task_repository,
        system_outbox_repository=system_outbox_repository,
    )
    db = FakeDb()
    session = _session()

    tasks = await service.request_operation_tasks(
        db,
        operation_key="op-repeated-ownerless",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=session,
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-repeated-ownerless",
        task_specs=[
            {
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            }
        ],
    )

    assert tasks == [existing_task]
    assert existing_outbox.session_id == session.id
    assert lifecycle.calls == []
    assert system_outbox_repository.locked_calls == [existing_task.dispatch_key]


@pytest.mark.asyncio
async def test_repeated_operation_rejects_existing_task_outbox_owned_by_different_session() -> None:
    task_repository = FakeRackTaskRepository()
    existing_task = task_repository.add_existing(
        operation_key="op-repeated-conflict",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code=CLASSIFIER_WORK_POSITION_CODE,
        target_position_role=CLASSIFIER_WORK_POSITION_ROLE,
        target_code=RACK_OPERATION_TARGET_CODE,
    )
    system_outbox_repository = FakeOutboxRepository()
    system_outbox_repository.add_existing(
        SystemOutbox(
            dispatch_key=existing_task.dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=RACK_OPERATION_TARGET_CODE,
            operation_domain="RACK",
            operation_key="op-repeated-conflict",
            workline_id=45,
            session_id=999,
            payload_json=dict(existing_task.request_json),
            status=SystemOutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        task_repository=task_repository,
        system_outbox_repository=system_outbox_repository,
    )

    with pytest.raises(ValueError, match="existing rack outbox belongs to another session"):
        await service.request_operation_tasks(
            FakeDb(),
            operation_key="op-repeated-conflict",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-repeated-conflict",
            task_specs=[
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []
    assert system_outbox_repository.locked_calls == [existing_task.dispatch_key]


@pytest.mark.asyncio
async def test_repeated_operation_rejects_missing_non_required_physical_task() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-repeat-missing-optional",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
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
                    "task_type": RackTaskType.MOVE_RACK.value,
                    "rack_code": "RACK-OLD",
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "source_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": MOVE_OUT_TARGET_POSITION_ROLE,
                    "required": False,
                },
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                },
            ],
        )

    assert lifecycle.calls == []
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_repeated_replace_operation_rejects_single_supply_with_wrong_sequence_no() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-shape-supply-sequence",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
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
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
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
        task_type=RackTaskType.MOVE_RACK,
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
            "task_type": RackTaskType.MOVE_RACK.value,
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
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
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
async def test_repeated_operation_rejects_request_json_payload_drift() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-request-json-drift",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
        request_json={
            "operation_key": "op-request-json-drift",
            "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
            "sequence_no": 2,
            "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
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

    with pytest.raises(ValueError, match="request_json differs from request"):
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
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                    "request_json": {"route_profile": "NEW"},
                }
            ],
        )


@pytest.mark.asyncio
async def test_repeated_operation_rejects_actions_json_payload_drift() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-actions-json-drift",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
        actions_json={
            "action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "required": True,
            "route_profile": "OLD",
        },
    )
    service, _repo, _lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        task_repository=task_repository,
    )

    with pytest.raises(ValueError, match="actions_json differs from request"):
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
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
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
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code=None,
        target_position_code="CLASSIFIER-WORK",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        target_code="WMS-RACK",
        request_json={
            "operation_key": "op-timeout-evidence",
            "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
            "sequence_no": 2,
            "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "workline_code": "WL-SMT-01",
            "source_position_code": None,
            "target_position_code": "CLASSIFIER-WORK",
            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
            "rack_kind": RackKind.SINGLE_LAYER.value,
            "rack_code": None,
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
    system_outbox_repository = FakeOutboxRepository()
    existing_outbox = system_outbox_repository.add_existing(
        SystemOutbox(
            id=88,
            session_id=300,
            workline_id=45,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key="rack-operation:op-existing-outbox:2:ALLOCATE_AND_MOVE_RACK",
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-existing-outbox",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-existing-outbox",
                "dispatch_key": "rack-operation:op-existing-outbox:2:ALLOCATE_AND_MOVE_RACK",
                "actions": {"action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
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
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_request_links_reused_ownerless_outbox_to_current_session() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-reused-ownerless:2:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = system_outbox_repository.add_existing(
        SystemOutbox(
            dispatch_key=dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=RACK_OPERATION_TARGET_CODE,
            operation_domain="RACK",
            operation_key="op-reused-ownerless",
            workline_id=45,
            session_id=None,
            payload_json={
                "dispatch_key": dispatch_key,
                "callback_type": "WMS_RACK_ARRIVED",
                "operation_key": "op-reused-ownerless",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )
    db = FakeDb()
    session = _session()

    await service.request_operation_tasks(
        db,
        operation_key="op-reused-ownerless",
        operation_type=RACK_TRANSPORT_OPERATION_TYPE,
        workline=_workline(),
        session=session,
        target_code=RACK_OPERATION_TARGET_CODE,
        trace_id="trace-reused-ownerless",
        task_specs=[
            {
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            }
        ],
    )

    assert existing_outbox.session_id == session.id
    assert lifecycle.calls[0]["outbox"] is existing_outbox
    assert existing_outbox.payload_json["request_id"] == dispatch_key
    assert existing_outbox.payload_json["callback_type"] == "WMS_RACK_ARRIVED"
    assert existing_outbox.payload_json["source"] == {"position_code": None}
    assert existing_outbox.payload_json["target"] == {
        "position_code": CLASSIFIER_WORK_POSITION_CODE,
        "position_role": CLASSIFIER_WORK_POSITION_ROLE,
    }
    assert existing_outbox.target_code == RACK_OPERATION_TARGET_CODE
    assert existing_outbox.payload_json["actions"] == {
        "action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
        "required": True,
    }
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_request_rejects_reused_finished_outbox() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-reused-finished:2:ALLOCATE_AND_MOVE_RACK"
    system_outbox_repository.add_existing(
        SystemOutbox(
            dispatch_key=dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=RACK_OPERATION_TARGET_CODE,
            operation_domain="RACK",
            operation_key="op-reused-finished",
            workline_id=45,
            session_id=300,
            payload_json={
                "dispatch_key": dispatch_key,
                "callback_type": "WMS_RACK_ARRIVED",
                "operation_key": "op-reused-finished",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            },
            status=SystemOutboxStatus.SENT,
            finished_at=timezone.now_for_db(),
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )

    with pytest.raises(ValueError, match="existing rack operation outbox is no longer active"):
        await service.request_operation_tasks(
            FakeDb(),
            operation_key="op-reused-finished",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-reused-finished",
            task_specs=[
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []


@pytest.mark.asyncio
async def test_request_rejects_reused_outbox_owned_by_different_session() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-reused-conflict:2:ALLOCATE_AND_MOVE_RACK"
    system_outbox_repository.add_existing(
        SystemOutbox(
            dispatch_key=dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=RACK_OPERATION_TARGET_CODE,
            operation_domain="RACK",
            operation_key="op-reused-conflict",
            workline_id=45,
            session_id=999,
            payload_json={
                "dispatch_key": dispatch_key,
                "callback_type": "WMS_RACK_ARRIVED",
                "operation_key": "op-reused-conflict",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )

    with pytest.raises(ValueError, match="existing rack outbox belongs to another session"):
        await service.request_operation_tasks(
            FakeDb(),
            operation_key="op-reused-conflict",
            operation_type=RACK_TRANSPORT_OPERATION_TYPE,
            workline=_workline(),
            session=_session(),
            target_code=RACK_OPERATION_TARGET_CODE,
            trace_id="trace-reused-conflict",
            task_specs=[
                {
                    "sequence_no": 2,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": RackKind.SINGLE_LAYER.value,
                    "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
                    "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
                }
            ],
        )

    assert lifecycle.calls == []


@pytest.mark.asyncio
async def test_request_reuses_existing_outbox_after_integrity_error_on_concurrent_insert() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-outbox-race:2:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = system_outbox_repository.add_existing(
        SystemOutbox(
            id=89,
            session_id=300,
            workline_id=45,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-outbox-race",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-outbox-race",
                "dispatch_key": dispatch_key,
                "actions": {"action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    system_outbox_repository.return_none_once_for.add(dispatch_key)
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
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
    assert system_outbox_repository.locked_calls == [dispatch_key, dispatch_key]
    assert db.nested_rollback_count == 1
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_request_links_ownerless_outbox_after_integrity_error_on_concurrent_insert() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-outbox-race-ownerless:2:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = system_outbox_repository.add_existing(
        SystemOutbox(
            id=89,
            session_id=None,
            workline_id=45,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-outbox-race-ownerless",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-outbox-race-ownerless",
                "dispatch_key": dispatch_key,
                "actions": {"action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    system_outbox_repository.return_none_once_for.add(dispatch_key)
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )
    db = FakeDb()
    db.fail_next_flush_with_integrity = True
    session = _session()

    tasks = await _request_classifier_replacement(
        service,
        db,
        operation_key="op-outbox-race-ownerless",
        session=session,
        trace_id="trace-outbox-race-ownerless",
        include_move_out=False,
    )

    assert len(tasks) == 1
    assert existing_outbox.session_id == session.id
    assert lifecycle.calls[0]["outbox"] is existing_outbox
    assert system_outbox_repository.locked_calls == [dispatch_key, dispatch_key]
    assert db.nested_rollback_count == 1
    assert db.flush_count == 2
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_request_rejects_conflicting_outbox_after_integrity_error_on_concurrent_insert() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-outbox-race-conflict:2:ALLOCATE_AND_MOVE_RACK"
    system_outbox_repository.add_existing(
        SystemOutbox(
            id=89,
            session_id=999,
            workline_id=45,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-outbox-race-conflict",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-outbox-race-conflict",
                "dispatch_key": dispatch_key,
                "actions": {"action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    system_outbox_repository.return_none_once_for.add(dispatch_key)
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )
    db = FakeDb()
    db.fail_next_flush_with_integrity = True

    with pytest.raises(ValueError, match="existing rack outbox belongs to another session"):
        await _request_classifier_replacement(
            service,
            db,
            operation_key="op-outbox-race-conflict",
            trace_id="trace-outbox-race-conflict",
            include_move_out=False,
        )

    assert lifecycle.calls == []
    assert system_outbox_repository.locked_calls == [dispatch_key, dispatch_key]
    assert db.nested_rollback_count == 1
    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []


@pytest.mark.asyncio
async def test_request_reuses_existing_outbox_when_only_trace_id_differs() -> None:
    system_outbox_repository = FakeOutboxRepository()
    dispatch_key = "rack-operation:op-existing-outbox-trace:2:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = system_outbox_repository.add_existing(
        SystemOutbox(
            id=90,
            session_id=300,
            workline_id=45,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS-RACK",
            payload_json={
                "operation_key": "op-existing-outbox-trace",
                "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
                "sequence_no": 2,
                "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                "workline_code": "WL-SMT-01",
                "rack_kind": RackKind.SINGLE_LAYER.value,
                "source_position_code": None,
                "target_position_code": "CLASSIFIER-WORK",
                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                "trace_id": "trace-old",
                "dispatch_key": dispatch_key,
                "actions": {"action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
            },
            status=SystemOutboxStatus.NEW,
        )
    )
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
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

    outboxes = [item for item in db.added if isinstance(item, SystemOutbox)]
    assert [(outbox.dispatch_type, outbox.target_type, outbox.status) for outbox in outboxes] == [
        (SystemOutboxDispatchType.EXTERNAL_HTTP, SystemOutboxTargetType.HTTP_ENDPOINT, SystemOutboxStatus.NEW),
        (SystemOutboxDispatchType.EXTERNAL_HTTP, SystemOutboxTargetType.HTTP_ENDPOINT, SystemOutboxStatus.NEW),
    ]


@pytest.mark.asyncio
async def test_derive_operation_status_requires_all_required_tasks_succeeded() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-status",
        sequence_no=1,
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        rack_code="RACK-OLD",
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
    )
    task_repository.add_existing(
        operation_key="op-status",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, placements = _service(task_repository=task_repository)

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-status") == RackOperationStatus.PENDING.value
    )

    task_repository.tasks[1].task_status = RackTaskStatus.SUCCEEDED
    placements.placements_by_position["CLASSIFIER-WORK"] = [_active_rack("RACK-NEW")]
    placements.placements_by_position["CLASSIFIER-WORK-SOURCE"] = []

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-status")
        == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_resource_projection_confirmation() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, placements = _service(task_repository=task_repository)

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection")
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [_active_rack("RACK-NEW")]

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection")
        == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_callback_trusted_skips_resource_projection_confirmation() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-callback-trusted",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        target_position_code="CLASSIFIER-WORK",
    )
    operation_repository = FakeRackOperationRepository(
        completion_policy=OperationCompletionPolicy.CALLBACK_TRUSTED,
    )
    operation_repository.operation.operation_key = "op-callback-trusted"
    service, _repo, _lifecycle, _placements = _service(
        task_repository=task_repository,
        rack_operation_repository=operation_repository,
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-callback-trusted")
        == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_system_rack_operation_without_workline_defaults_to_resource_projection_completion_policy() -> None:
    operation_repository = FakeRackOperationRepository(
        completion_policy=OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION,
    )
    service, _repo, _lifecycle, _placements = _service(rack_operation_repository=operation_repository)

    await service.request_operation_tasks(
        FakeDb(),
        operation_key="system-rack-rebalance-001",
        operation_type="GLOBAL_RACK_REBALANCE",
        workline=None,
        session=None,
        target_code="WMS_RCS_RACK_OPERATION",
        trace_id="trace-system-rack",
        task_specs=[
            {
                "sequence_no": 1,
                "task_type": RackTaskType.MOVE_RACK.value,
                "rack_code": "RACK-A",
                "source_position_code": "AREA-A-01",
                "target_position_code": "AREA-B-01",
            }
        ],
    )

    assert operation_repository.operation.completion_policy == OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED


def test_rack_operation_model_defaults_to_resource_projection_completion_policy() -> None:
    operation = RackOperationBase(operation_key="rack-op-default", operation_type="GLOBAL_RACK_REBALANCE")

    assert operation.completion_policy == OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED


@pytest.mark.asyncio
async def test_derive_operation_status_callback_plus_reconciliation_succeeds_without_projection_confirmation() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-callback-plus-reconciliation",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    operation_repository = FakeRackOperationRepository(
        completion_policy=OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION,
    )
    operation_repository.operation.operation_key = "op-callback-plus-reconciliation"
    service, _repo, _lifecycle, _placements = _service(
        task_repository=task_repository,
        rack_operation_repository=operation_repository,
        placements_by_position={"CLASSIFIER-WORK": []},
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-callback-plus-reconciliation")
        == RackOperationStatus.SUCCEEDED.value
    )

    persisted_status = await service._persist_operation_status(
        FakeDb(),
        operation_key="op-callback-plus-reconciliation",
    )

    assert persisted_status == RackOperationStatus.SUCCEEDED.value
    assert operation_repository.operation.result_json["reconciliation_expected"] is True


@pytest.mark.asyncio
async def test_derive_operation_status_reconciles_when_projection_rack_kind_mismatches() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection-kind",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, _placements = _service(
        task_repository=task_repository,
        placements_by_position={"CLASSIFIER-WORK": [_active_rack("RACK-WRONG", rack_kind=RackKind.FIVE_LAYER.value)]},
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection-kind")
        == RackOperationStatus.RECONCILING.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_accepts_enum_rack_kind_projection() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection-kind-enum",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    service, _repo, _lifecycle, _placements = _service(
        task_repository=task_repository,
        placements_by_position={"CLASSIFIER-WORK": [_active_rack("RACK-ENUM", rack_kind=RackKind.SINGLE_LAYER)]},
    )

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection-kind-enum")
        == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_consumes_projection_per_inbound_task() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-projection-count",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        rack_code=None,
        rack_kind=RackKind.SINGLE_LAYER.value,
        target_position_code="CLASSIFIER-WORK",
    )
    task_repository.add_existing(
        operation_key="op-projection-count",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
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
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [
        _active_rack("RACK-NEW-1"),
        _active_rack("RACK-NEW-2"),
    ]

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-projection-count")
        == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_move_rack_target_projection_confirmation() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-move-target",
        sequence_no=1,
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
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
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["TARGET-POSITION"] = [_active_rack("RACK-MOVED")]

    assert (
        await service.derive_operation_status(FakeDb(), operation_key="op-move-target")
        == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_reconciles_when_move_out_rack_still_at_source_position() -> None:
    task_repository = FakeRackTaskRepository()
    task_repository.add_existing(
        operation_key="op-source-not-cleared",
        sequence_no=1,
        task_type=RackTaskType.MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
        rack_code="RACK-OLD",
        rack_kind=RackKind.SINGLE_LAYER.value,
        source_position_code="CLASSIFIER-WORK",
        target_position_code=None,
    )
    task_repository.add_existing(
        operation_key="op-source-not-cleared",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.SUCCEEDED,
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
        == RackOperationStatus.RECONCILING.value
    )
