"""工作线货架业务操作编排服务。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.app.resource.models import RackKind
from src.app.resource.repositories.resource_repository import (
    RackPlacementRepository,
    rack_placement_repository,
)
from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType, WorklineOutbox
from src.app.workline.models.rack_task import WorklineRackTaskStatus, WorklineRackTaskType
from src.app.workline.models.session import SessionStatus
from src.app.workline.repositories.outbox_repository import WorklineOutboxRepository, outbox_repository
from src.app.workline.repositories.rack_task_repository import (
    WorklineRackTaskRepository,
    workline_rack_task_repository,
)
from src.app.workline.services.rack_position_service import (
    WorklineRackPositionService,
    workline_rack_position_service,
)
from src.app.workline.services.rack_task_service import (
    WorklineRackTaskLifecycleService,
    workline_rack_task_lifecycle_service,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS = 300


class WorklineRackOperationType(str, Enum):
    """货架业务操作类型。"""

    REPLACE_CLASSIFIER_WORK_RACK = "REPLACE_CLASSIFIER_WORK_RACK"
    MOVE_RACK_TO_POSITION = "MOVE_RACK_TO_POSITION"
    ALLOCATE_RACK_TO_POSITION = "ALLOCATE_RACK_TO_POSITION"
    TURN_RACK_SIDE = "TURN_RACK_SIDE"


class WorklineRackOperationStatus(str, Enum):
    """货架业务操作派生状态。"""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True)
class WorklineRackTaskSpec:
    """货架 operation 拆分后的低级 task 描述。"""

    sequence_no: int
    task_type: str
    rack_code: str | None
    rack_kind: str | None
    source_position_code: str | None
    target_position_code: str | None
    target_position_role: str | None
    dispatch_key: str
    target_code: str
    request_json: dict[str, Any]
    actions_json: dict[str, Any]
    required: bool


class WorklineRackOperationService:
    """工作线货架业务操作服务。

    该服务只负责短事务内的容量校验、task/outbox 创建和 session 等待标记；
    外部 HTTP、Celery 派发和 WMS/RCS SDK 调用由既有 outbox 流程处理。
    """

    def __init__(
        self,
        *,
        rack_task_repository: WorklineRackTaskRepository = workline_rack_task_repository,
        rack_task_lifecycle_service: WorklineRackTaskLifecycleService = workline_rack_task_lifecycle_service,
        outbox_repository: WorklineOutboxRepository = outbox_repository,
        rack_position_service: WorklineRackPositionService = workline_rack_position_service,
        rack_placement_repository: RackPlacementRepository = rack_placement_repository,
    ) -> None:
        self.rack_task_repository = rack_task_repository
        self.rack_task_lifecycle_service = rack_task_lifecycle_service
        self.outbox_repository = outbox_repository
        self.rack_position_service = rack_position_service
        self.rack_placement_repository = rack_placement_repository

    async def request_replace_classifier_work_rack(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline: Any,
        session: Any,
        work_position_code: str,
        new_rack_kind: RackKind | str,
        move_out_target_position_role: str,
        supply_target_code: str,
        trace_id: str,
    ) -> list[Any]:
        """请求“粗分机当前工作位换新空箱货架”操作。"""

        operation_key = _required_text(operation_key, "operation_key")
        workline_code = _required_text(getattr(workline, "line_code", None), "workline.line_code")
        work_position_code = _required_text(work_position_code, "work_position_code")
        move_out_target_position_role = _required_text(
            move_out_target_position_role,
            "move_out_target_position_role",
        )
        supply_target_code = _required_text(supply_target_code, "supply_target_code")
        trace_id = _required_text(trace_id, "trace_id")
        rack_kind = _rack_kind(new_rack_kind)

        existing_tasks = await self.rack_task_repository.list_by_operation_key(db, operation_key=operation_key)
        if existing_tasks:
            _ensure_existing_operation_request_consistent(
                existing_tasks,
                operation_key=operation_key,
                operation_type=WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
                work_position_code=work_position_code,
                new_rack_kind=rack_kind.value,
                move_out_target_position_role=move_out_target_position_role,
                supply_target_code=supply_target_code,
            )
            return list(existing_tasks)

        active_placements = await self.rack_placement_repository.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=work_position_code,
        )
        if len(active_placements) > 1:
            raise ValueError(
                f"classifier work position has multiple active racks: {workline_code}/{work_position_code}"
            )

        _position, capacity = await self.rack_position_service.require_position_capacity_for_update(
            db,
            workline_code=workline_code,
            position_code=work_position_code,
            rack_kind=rack_kind,
        )
        specs = self._replace_classifier_specs(
            operation_key=operation_key,
            workline_code=workline_code,
            work_position_code=work_position_code,
            new_rack_kind=rack_kind,
            move_out_target_position_role=move_out_target_position_role,
            supply_target_code=supply_target_code,
            trace_id=trace_id,
            active_rack=active_placements[0] if active_placements else None,
        )

        await self._ensure_capacity_for_supply(
            db,
            operation_key=operation_key,
            workline_code=workline_code,
            target_position_code=work_position_code,
            capacity=capacity,
            specs=specs,
        )

        created_tasks: list[Any] = []
        for spec in specs:
            outbox = await self._get_or_create_outbox(
                db,
                session=session,
                workline=workline,
                spec=spec,
            )
            task = await self.rack_task_lifecycle_service.record_requested_task(
                db,
                session=session,
                workline=workline,
                outbox=outbox,
                operation_key=operation_key,
                operation_type=WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
                sequence_no=spec.sequence_no,
                task_type=spec.task_type,
                task_key=spec.dispatch_key,
                dispatch_key=spec.dispatch_key,
                target_code=spec.target_code,
                request_json=spec.request_json,
                timeout_seconds=DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS,
                source_system="WMS_RCS",
                trace_id=trace_id,
                rack_kind=spec.rack_kind,
                rack_code=spec.rack_code,
                source_position_code=spec.source_position_code,
                target_position_code=spec.target_position_code,
                target_position_role=spec.target_position_role,
                actions_json=spec.actions_json,
            )
            created_tasks.append(task)

        self._mark_session_waiting_for_operation(
            session,
            operation_key=operation_key,
            operation_type=WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value,
            task_specs=specs,
        )
        db.add(session)
        return created_tasks

    async def _get_or_create_outbox(
        self,
        db: AsyncSession,
        *,
        session: Any,
        workline: Any,
        spec: WorklineRackTaskSpec,
    ) -> WorklineOutbox:
        payload_json = _outbox_payload(spec)
        existing = await self.outbox_repository.get_by_dispatch_key(db, spec.dispatch_key)
        if existing is not None:
            _ensure_existing_outbox_shape(existing, spec=spec, payload_json=payload_json)
            return existing

        outbox = WorklineOutbox(
            session_id=_optional_int(getattr(session, "id", None)),
            workline_id=_required_int(getattr(workline, "id", None), "workline.id"),
            dispatch_type=DispatchType.EXTERNAL_HTTP,
            dispatch_key=spec.dispatch_key,
            target_type=TargetType.HTTP_ENDPOINT,
            target_code=spec.target_code,
            payload_json=payload_json,
            status=OutboxStatus.NEW,
        )
        try:
            async with db.begin_nested():
                db.add(outbox)
                await db.flush()
        except IntegrityError:
            existing = await self.outbox_repository.get_by_dispatch_key(db, spec.dispatch_key)
            if existing is None:
                raise
            _ensure_existing_outbox_shape(existing, spec=spec, payload_json=payload_json)
            return existing
        return outbox

    async def derive_operation_status(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
    ) -> str:
        """按 sibling task 与 resource projection 派生 operation 状态。"""

        tasks = await self.rack_task_repository.list_by_operation_key(
            db,
            operation_key=_required_text(operation_key, "operation_key"),
        )
        required_tasks = [task for task in tasks if _task_required(task)]
        if not required_tasks:
            return WorklineRackOperationStatus.PENDING.value

        statuses = {_task_status(task) for task in required_tasks}
        if statuses & {
            WorklineRackTaskStatus.FAILED.value,
            WorklineRackTaskStatus.TIMEOUT.value,
            WorklineRackTaskStatus.CANCELLED.value,
        }:
            return WorklineRackOperationStatus.FAILED.value
        if WorklineRackTaskStatus.RECONCILING.value in statuses:
            return WorklineRackOperationStatus.RECONCILING.value
        if statuses & {
            WorklineRackTaskStatus.PLANNED.value,
            WorklineRackTaskStatus.REQUESTED.value,
            WorklineRackTaskStatus.IN_PROGRESS.value,
        }:
            return WorklineRackOperationStatus.PENDING.value

        if not await self._resource_projection_confirms_success(db, required_tasks):
            return WorklineRackOperationStatus.RECONCILING.value
        return WorklineRackOperationStatus.SUCCEEDED.value

    def _replace_classifier_specs(
        self,
        *,
        operation_key: str,
        workline_code: str,
        work_position_code: str,
        new_rack_kind: RackKind,
        move_out_target_position_role: str,
        supply_target_code: str,
        trace_id: str,
        active_rack: Any | None,
    ) -> list[WorklineRackTaskSpec]:
        specs: list[WorklineRackTaskSpec] = []
        operation_type = WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value
        if active_rack is not None:
            specs.append(
                self._task_spec(
                    operation_key=operation_key,
                    operation_type=operation_type,
                    sequence_no=1,
                    task_type=WorklineRackTaskType.MOVE_RACK.value,
                    workline_code=workline_code,
                    rack_code=_optional_str(getattr(active_rack, "rack_code", None)),
                    rack_kind=_optional_str(getattr(active_rack, "rack_kind", None)),
                    source_position_code=work_position_code,
                    target_position_code=None,
                    target_position_role=move_out_target_position_role,
                    target_code=supply_target_code,
                    trace_id=trace_id,
                )
            )
        specs.append(
            self._task_spec(
                operation_key=operation_key,
                operation_type=operation_type,
                sequence_no=2,
                task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                workline_code=workline_code,
                rack_code=None,
                rack_kind=new_rack_kind.value,
                source_position_code=None,
                target_position_code=work_position_code,
                target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
                target_code=supply_target_code,
                trace_id=trace_id,
            )
        )
        return specs

    def _task_spec(
        self,
        *,
        operation_key: str,
        operation_type: str,
        sequence_no: int,
        task_type: str,
        workline_code: str,
        rack_code: str | None,
        rack_kind: str | None,
        source_position_code: str | None,
        target_position_code: str | None,
        target_position_role: str | None,
        target_code: str,
        trace_id: str,
    ) -> WorklineRackTaskSpec:
        dispatch_key = f"rack-operation:{operation_key}:{sequence_no}:{task_type}"
        request_json = {
            "operation_key": operation_key,
            "operation_type": operation_type,
            "sequence_no": sequence_no,
            "task_type": task_type,
            "workline_code": workline_code,
            "rack_code": rack_code,
            "rack_kind": rack_kind,
            "source_position_code": source_position_code,
            "target_position_code": target_position_code,
            "target_position_role": target_position_role,
            "trace_id": trace_id,
        }
        actions_json = {"action": task_type, "required": True}
        return WorklineRackTaskSpec(
            sequence_no=sequence_no,
            task_type=task_type,
            rack_code=rack_code,
            rack_kind=rack_kind,
            source_position_code=source_position_code,
            target_position_code=target_position_code,
            target_position_role=target_position_role,
            dispatch_key=dispatch_key,
            target_code=target_code,
            request_json=request_json,
            actions_json=actions_json,
            required=True,
        )

    async def _ensure_capacity_for_supply(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str,
        target_position_code: str,
        capacity: int,
        specs: list[WorklineRackTaskSpec],
    ) -> None:
        active_count = await self.rack_placement_repository.count_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=target_position_code,
        )
        same_operation_release_count = await self._same_operation_release_count(
            db,
            operation_key=operation_key,
            source_position_code=target_position_code,
            specs=specs,
        )
        other_operation_active_target_task_count = await self._other_operation_active_target_task_count(
            db,
            operation_key=operation_key,
            workline_code=workline_code,
            target_position_code=target_position_code,
        )
        available_capacity_for_operation = (
            capacity - active_count - other_operation_active_target_task_count + same_operation_release_count
        )
        if available_capacity_for_operation <= 0:
            raise ValueError(
                "rack operation target position capacity unavailable: "
                f"{workline_code}/{target_position_code} "
                f"capacity={capacity} active={active_count} "
                f"other_operation_target_tasks={other_operation_active_target_task_count} "
                f"same_operation_release={same_operation_release_count}"
            )

    async def _same_operation_release_count(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        source_position_code: str,
        specs: list[WorklineRackTaskSpec],
    ) -> int:
        current_specs_count = sum(
            1
            for spec in specs
            if spec.required
            and spec.task_type == WorklineRackTaskType.MOVE_RACK.value
            and spec.source_position_code == source_position_code
        )
        existing_tasks = await self.rack_task_repository.list_by_operation_key(db, operation_key=operation_key)
        existing_count = sum(
            1
            for task in existing_tasks
            if _task_required(task)
            and _task_type(task) == WorklineRackTaskType.MOVE_RACK.value
            and getattr(task, "source_position_code", None) == source_position_code
            and _task_status(task)
            in {
                WorklineRackTaskStatus.PLANNED.value,
                WorklineRackTaskStatus.REQUESTED.value,
                WorklineRackTaskStatus.IN_PROGRESS.value,
                WorklineRackTaskStatus.SUCCEEDED.value,
            }
        )
        return current_specs_count + existing_count

    async def _other_operation_active_target_task_count(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str,
        target_position_code: str,
    ) -> int:
        active_target_tasks = await self.rack_task_repository.list_active_by_target_position(
            db,
            workline_code=workline_code,
            target_position_code=target_position_code,
        )
        return sum(
            1
            for task in active_target_tasks
            if getattr(task, "operation_key", None) != operation_key
            and _task_required(task)
            and _task_occupies_target_position(task, target_position_code=target_position_code)
        )

    async def _resource_projection_confirms_success(self, db: AsyncSession, tasks: list[Any]) -> bool:
        move_out_rack_codes = {
            rack_code
            for task in tasks
            if _task_type(task) == WorklineRackTaskType.MOVE_RACK.value
            for rack_code in [_optional_str(getattr(task, "rack_code", None))]
            if rack_code is not None
        }

        for task in tasks:
            if _task_type(task) == WorklineRackTaskType.MOVE_RACK.value:
                if await self._move_out_rack_still_at_source(db, task):
                    return False
                continue

            target_position_code = _optional_str(getattr(task, "target_position_code", None))
            if _task_type(task) != WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value and target_position_code is None:
                continue
            if target_position_code is None:
                return False
            workline_code = _optional_str(getattr(task, "workline_code", None))
            if workline_code is None:
                return False
            placements = await self.rack_placement_repository.list_active_by_workline_position(
                db,
                workline_code=workline_code,
                position_code=target_position_code,
            )
            if not _target_projection_matches_supply(task, placements, move_out_rack_codes=move_out_rack_codes):
                return False
        return True

    async def _move_out_rack_still_at_source(self, db: AsyncSession, task: Any) -> bool:
        rack_code = _optional_str(getattr(task, "rack_code", None))
        source_position_code = _optional_str(getattr(task, "source_position_code", None))
        workline_code = _optional_str(getattr(task, "workline_code", None))
        if rack_code is None or source_position_code is None or workline_code is None:
            return False
        placements = await self.rack_placement_repository.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=source_position_code,
        )
        return any(_optional_str(getattr(placement, "rack_code", None)) == rack_code for placement in placements)

    def _mark_session_waiting_for_operation(
        self,
        session: Any,
        *,
        operation_key: str,
        operation_type: str,
        task_specs: list[WorklineRackTaskSpec],
    ) -> None:
        now = timezone.now_for_db()
        context_json = dict(getattr(session, "context_json", None) or {})
        existing_operation = context_json.get("rack_operation")
        rack_operation = dict(existing_operation) if isinstance(existing_operation, Mapping) else {}
        task_dispatch_keys = [spec.dispatch_key for spec in task_specs]
        required_task_dispatch_keys = [spec.dispatch_key for spec in task_specs if spec.required]
        released_rack_codes = [
            rack_code
            for spec in task_specs
            if spec.task_type == WorklineRackTaskType.MOVE_RACK.value
            and (rack_code := _optional_str(spec.rack_code)) is not None
        ]
        rack_operation.update(
            {
                "operation_key": operation_key,
                "operation_type": operation_type,
                "status": WorklineRackOperationStatus.PENDING.value,
                "task_count": len(task_specs),
                "required_task_count": sum(1 for spec in task_specs if spec.required),
                "task_sequences": [spec.sequence_no for spec in task_specs],
                "task_dispatch_keys": task_dispatch_keys,
                "required_task_dispatch_keys": required_task_dispatch_keys,
                "released_rack_codes": released_rack_codes,
            }
        )
        context_json["waiting_rack_operation_key"] = operation_key
        context_json["rack_operation"] = rack_operation
        session.context_json = context_json
        session.status = SessionStatus.WAITING_EXTERNAL
        session.current_wait_type = "RACK_OPERATION"
        session.waiting_since = now
        session.current_wait_timeout_seconds = DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS
        session.deadline_at = now + timedelta(seconds=DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS)
        session.awaiting_command_id = None
        session.ended_at = None
        session.failure_domain = None
        session.failure_code = None
        session.failure_message = None


def _ensure_existing_operation_shape(
    tasks: list[Any],
    specs: list[WorklineRackTaskSpec],
    *,
    operation_type: str,
) -> None:
    required_tasks = sorted((task for task in tasks if _task_required(task)), key=lambda task: task.sequence_no)
    required_specs = sorted((spec for spec in specs if spec.required), key=lambda spec: spec.sequence_no)
    if len(required_tasks) != len(required_specs):
        raise ValueError("existing rack operation task count differs from request")

    for task, spec in zip(required_tasks, required_specs, strict=True):
        if getattr(task, "operation_type", None) != WorklineRackOperationType.REPLACE_CLASSIFIER_WORK_RACK.value:
            raise ValueError("existing rack operation type differs from request")
        if getattr(task, "operation_type", None) != operation_type:
            raise ValueError("existing rack operation type differs from request")
        if getattr(task, "sequence_no", None) != spec.sequence_no:
            raise ValueError("existing rack operation sequence_no differs from request")
        if _task_type(task) != spec.task_type:
            raise ValueError("existing rack operation required task type differs from request")
        if _task_required(task) != spec.required:
            raise ValueError("existing rack operation required flag differs from request")
        if (
            getattr(task, "source_position_code", None) != spec.source_position_code
            or getattr(task, "target_position_code", None) != spec.target_position_code
            or getattr(task, "target_position_role", None) != spec.target_position_role
        ):
            raise ValueError("existing rack operation source/target position differs from request")
        if getattr(task, "rack_kind", None) != spec.rack_kind:
            raise ValueError("existing rack operation new_rack_kind differs from request")

    supply_specs = [
        spec for spec in required_specs if spec.task_type == WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value
    ]
    if len(supply_specs) != 1 or supply_specs[0].sequence_no != 2:
        raise ValueError("existing rack operation required task type differs from request")


def _ensure_existing_operation_request_consistent(
    tasks: list[Any],
    *,
    operation_key: str,
    operation_type: str,
    work_position_code: str,
    new_rack_kind: str,
    move_out_target_position_role: str,
    supply_target_code: str,
) -> None:
    required_tasks = sorted((task for task in tasks if _task_required(task)), key=lambda task: task.sequence_no)
    task_types = tuple(_task_type(task) for task in required_tasks)
    if task_types not in {
        (WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value,),
        (WorklineRackTaskType.MOVE_RACK.value, WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value),
    }:
        raise ValueError("existing rack operation required task type differs from request")

    for task in required_tasks:
        sequence_no = getattr(task, "sequence_no", None)
        task_type = _task_type(task)
        expected_key = f"rack-operation:{operation_key}:{sequence_no}:{task_type}"
        if getattr(task, "operation_key", None) != operation_key:
            raise ValueError("existing rack operation key differs from request")
        if getattr(task, "operation_type", None) != operation_type:
            raise ValueError("existing rack operation type differs from request")
        if getattr(task, "dispatch_key", None) != expected_key:
            raise ValueError("existing rack operation dispatch_key differs from request")
        if getattr(task, "task_key", None) != expected_key:
            raise ValueError("existing rack operation task_key differs from request")
        if getattr(task, "target_code", None) != supply_target_code:
            raise ValueError("existing rack operation target_code differs from request")
        _ensure_task_request_json_matches(task, operation_key=operation_key, operation_type=operation_type)

    supply_tasks = [
        task for task in required_tasks if _task_type(task) == WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value
    ]
    if len(supply_tasks) != 1:
        raise ValueError("existing rack operation required task type differs from request")
    supply_task = supply_tasks[0]
    if getattr(supply_task, "sequence_no", None) != 2:
        raise ValueError("existing rack operation sequence_no differs from request")
    if (
        getattr(supply_task, "source_position_code", None) is not None
        or getattr(supply_task, "target_position_code", None) != work_position_code
        or getattr(supply_task, "target_position_role", None) != "SMT_CLASSIFIER_SINGLE_RACK_WORK"
    ):
        raise ValueError("existing rack operation source/target position differs from request")
    if getattr(supply_task, "rack_kind", None) != new_rack_kind:
        raise ValueError("existing rack operation new_rack_kind differs from request")

    move_tasks = [task for task in required_tasks if _task_type(task) == WorklineRackTaskType.MOVE_RACK.value]
    if move_tasks:
        move_task = move_tasks[0]
        request_rack_code = _request_json_value(move_task, "rack_code")
        if request_rack_code is not None and getattr(move_task, "rack_code", None) != request_rack_code:
            raise ValueError("existing rack operation move-out rack_code differs from request")
        if (
            getattr(move_task, "source_position_code", None) != work_position_code
            or getattr(move_task, "target_position_code", None) is not None
            or getattr(move_task, "target_position_role", None) != move_out_target_position_role
        ):
            raise ValueError("existing rack operation source/target position differs from request")


def _request_json_value(task: Any, key: str) -> Any:
    request_json = getattr(task, "request_json", None)
    if not isinstance(request_json, dict):
        return None
    return request_json.get(key)


def _ensure_task_request_json_matches(task: Any, *, operation_key: str, operation_type: str) -> None:
    request_json = getattr(task, "request_json", None)
    if not isinstance(request_json, dict):
        raise TypeError("existing rack operation request_json missing")

    expected = {
        "operation_key": operation_key,
        "operation_type": operation_type,
        "sequence_no": getattr(task, "sequence_no", None),
        "task_type": _task_type(task),
        "source_position_code": getattr(task, "source_position_code", None),
        "target_position_code": getattr(task, "target_position_code", None),
        "target_position_role": getattr(task, "target_position_role", None),
        "rack_kind": getattr(task, "rack_kind", None),
    }
    if _task_type(task) == WorklineRackTaskType.MOVE_RACK.value:
        expected["rack_code"] = getattr(task, "rack_code", None)

    for key, value in expected.items():
        if request_json.get(key) != value:
            raise ValueError(f"existing rack operation request_json {key} differs from request")


def _target_projection_matches_supply(
    task: Any,
    placements: list[Any],
    *,
    move_out_rack_codes: set[str],
) -> bool:
    task_rack_kind = _optional_str(getattr(task, "rack_kind", None))
    if task_rack_kind is None:
        return False
    task_rack_code = _optional_str(getattr(task, "rack_code", None))
    for placement in placements:
        placement_rack_code = _optional_str(getattr(placement, "rack_code", None))
        if placement_rack_code in move_out_rack_codes:
            continue
        if _optional_str(getattr(placement, "rack_kind", None)) != task_rack_kind:
            continue
        if task_rack_code is None or placement_rack_code == task_rack_code:
            return True
    return False


def _outbox_payload(spec: WorklineRackTaskSpec) -> dict[str, Any]:
    return {
        **spec.request_json,
        "dispatch_key": spec.dispatch_key,
        "actions": spec.actions_json,
    }


def _ensure_existing_outbox_shape(
    outbox: WorklineOutbox,
    *,
    spec: WorklineRackTaskSpec,
    payload_json: dict[str, Any],
) -> None:
    if outbox.dispatch_type != DispatchType.EXTERNAL_HTTP:
        raise ValueError("existing rack operation outbox dispatch_type differs from request")
    if outbox.target_type != TargetType.HTTP_ENDPOINT:
        raise ValueError("existing rack operation outbox target_type differs from request")
    if outbox.target_code != spec.target_code:
        raise ValueError("existing rack operation outbox target_code differs from request")

    existing_payload = outbox.payload_json if isinstance(outbox.payload_json, dict) else {}
    for key, value in payload_json.items():
        if key == "trace_id":
            continue
        if existing_payload.get(key) != value:
            raise ValueError(f"existing rack operation outbox payload {key} differs from request")


def _task_occupies_target_position(task: Any, *, target_position_code: str) -> bool:
    task_type = _task_type(task)
    if task_type == WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value:
        return True
    return (
        task_type == WorklineRackTaskType.MOVE_RACK.value
        and getattr(task, "target_position_code", None) == target_position_code
    )


def _task_required(task: Any) -> bool:
    actions_json = getattr(task, "actions_json", None)
    if isinstance(actions_json, dict) and "required" in actions_json:
        return bool(actions_json["required"])
    return True


def _task_status(task: Any) -> str | None:
    return _enum_value(getattr(task, "task_status", None))


def _task_type(task: Any) -> str | None:
    return _enum_value(getattr(task, "task_type", None))


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _rack_kind(value: RackKind | str) -> RackKind:
    raw_value = _enum_value(value)
    try:
        return RackKind(str(raw_value))
    except ValueError as exc:
        raise ValueError(f"unsupported rack_kind: {raw_value}") from exc


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_str(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(_enum_value(value)).strip()
    return text or None


def _required_int(value: Any, field_name: str) -> int:
    number = _optional_int(value)
    if number is None:
        raise ValueError(f"{field_name} is required")
    return number


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


workline_rack_operation_service = WorklineRackOperationService()


__all__ = [
    "DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS",
    "WorklineRackOperationService",
    "WorklineRackOperationStatus",
    "WorklineRackOperationType",
    "WorklineRackTaskSpec",
    "workline_rack_operation_service",
]
