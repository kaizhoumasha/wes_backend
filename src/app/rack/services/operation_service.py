"""货架业务 operation 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.rack.models.operation import RackOperationStatus, RackTaskType
from src.app.rack.repositories.operation_repository import (
    RackOperationRepository,
    RackTaskRepository,
    rack_operation_repository,
    rack_task_repository,
)
from src.app.rack.services.completion_policy import (
    derive_required_task_status,
    requires_resource_projection_confirmation,
    resolve_operation_completion_policy,
)
from src.app.resource.repositories.resource_repository import (
    RackPlacementRepository,
    rack_placement_repository,
)
from src.app.sys.models.outbox import OperationCompletionPolicy
from src.utils.value_normalization import coerce_optional_str, enum_value, require_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS = 300


class RackOperationMigrationRequiredError(RuntimeError):
    """Rack operation 尚未迁移到 typed T5 dispatcher。"""


@dataclass(frozen=True)
class RackTaskSpec:
    """货架 operation 的领域 task 描述；当前只保留读取侧类型合同。"""

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
    canonical_payload_bytes: bytes | None = None
    payload_hash: str | None = None


class RackOperationService:
    """货架业务 operation 服务；写入侧在 T5 前明确 fail closed。"""

    def __init__(
        self,
        *,
        rack_task_repository: RackTaskRepository = rack_task_repository,
        rack_operation_repository: RackOperationRepository = rack_operation_repository,
        rack_placement_repository: RackPlacementRepository = rack_placement_repository,
    ) -> None:
        self.rack_task_repository = rack_task_repository
        self.rack_operation_repository = rack_operation_repository
        self.rack_placement_repository = rack_placement_repository

    async def request_operation_tasks(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        operation_type: str,
        workline: Any | None = None,
        session: Any | None,
        target_code: str | None = None,
        trace_id: str,
        task_specs: Sequence[Mapping[str, Any] | RackTaskSpec],
        completion_policy: OperationCompletionPolicy | str | None = None,
        timeout_seconds: int = DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS,
    ) -> list[Any]:
        """T5 dispatcher 实现前拒绝创建 Rack operation、task 或 outbox。"""

        del (
            db,
            operation_key,
            operation_type,
            workline,
            session,
            target_code,
            trace_id,
            task_specs,
            completion_policy,
            timeout_seconds,
        )
        raise RackOperationMigrationRequiredError("legacy rack transport is removed; T5 dispatcher is not implemented")

    async def derive_operation_status(self, db: AsyncSession, *, operation_key: str) -> str:
        """按 sibling task 与资源投影派生既有 operation 状态。"""

        operation_key = require_text(operation_key, "operation_key")
        operation = await self.rack_operation_repository.get_by_operation_key(db, operation_key)
        completion_policy = resolve_operation_completion_policy(operation)
        tasks = await self.rack_task_repository.list_by_operation_key(db, operation_key=operation_key)
        required_tasks = [task for task in tasks if _task_required(task)]
        derived_status = derive_required_task_status(required_tasks)
        if derived_status is not None:
            return derived_status.value
        if requires_resource_projection_confirmation(
            completion_policy
        ) and not await self._resource_projection_confirms_success(db, required_tasks):
            return RackOperationStatus.RECONCILING.value
        return RackOperationStatus.SUCCEEDED.value

    async def _persist_operation_status(self, db: AsyncSession, *, operation_key: str) -> str:
        return await self.sync_operation_status(db, operation_key=operation_key)

    async def sync_operation_status(self, db: AsyncSession, *, operation_key: str) -> str:
        """在同一事务中回写既有 RackOperation 的派生状态。"""

        operation = await self.rack_operation_repository.get_by_operation_key(db, operation_key)
        completion_policy = resolve_operation_completion_policy(operation)
        operation_status = await self.derive_operation_status(db, operation_key=operation_key)
        result_json_patch = {}
        if (
            completion_policy == OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION
            and operation_status == RackOperationStatus.SUCCEEDED.value
        ):
            result_json_patch["reconciliation_expected"] = True
        _ = await self.rack_operation_repository.mark_status(
            db,
            operation_key=operation_key,
            operation_status=operation_status,
            result_json_patch=result_json_patch,
        )
        return operation_status

    async def _resource_projection_confirms_success(self, db: AsyncSession, tasks: list[Any]) -> bool:
        move_out_rack_codes = {
            rack_code
            for task in tasks
            if _task_type(task) == RackTaskType.MOVE_RACK.value
            for rack_code in [coerce_optional_str(getattr(task, "rack_code", None))]
            if rack_code is not None
        }
        target_tasks_by_position: dict[tuple[str, str], list[Any]] = {}
        for task in tasks:
            task_type = _task_type(task)
            if task_type == RackTaskType.MOVE_RACK.value and await self._move_out_rack_still_at_source(db, task):
                return False
            target_position_code = coerce_optional_str(getattr(task, "target_position_code", None))
            if target_position_code is None:
                if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value:
                    return False
                continue
            workline_code = coerce_optional_str(getattr(task, "workline_code", None))
            if workline_code is None:
                return False
            target_tasks_by_position.setdefault((workline_code, target_position_code), []).append(task)

        for (workline_code, target_position_code), target_tasks in target_tasks_by_position.items():
            placements = await self.rack_placement_repository.list_active_by_workline_position(
                db,
                workline_code=workline_code,
                position_code=target_position_code,
            )
            available_placements = list(placements)
            for task in target_tasks:
                if not _consume_target_projection(task, available_placements, move_out_rack_codes=move_out_rack_codes):
                    return False
        return True

    async def _move_out_rack_still_at_source(self, db: AsyncSession, task: Any) -> bool:
        rack_code = coerce_optional_str(getattr(task, "rack_code", None))
        source_position_code = coerce_optional_str(getattr(task, "source_position_code", None))
        workline_code = coerce_optional_str(getattr(task, "workline_code", None))
        if rack_code is None or source_position_code is None or workline_code is None:
            return False
        placements = await self.rack_placement_repository.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=source_position_code,
        )
        return any(coerce_optional_str(getattr(placement, "rack_code", None)) == rack_code for placement in placements)


def _consume_target_projection(task: Any, placements: list[Any], *, move_out_rack_codes: set[str]) -> bool:
    for index, placement in enumerate(placements):
        if _target_projection_matches_task(task, placement, move_out_rack_codes=move_out_rack_codes):
            placements.pop(index)
            return True
    return False


def _target_projection_matches_task(task: Any, placement: Any, *, move_out_rack_codes: set[str]) -> bool:
    task_type = _task_type(task)
    task_rack_kind = coerce_optional_str(enum_value(getattr(task, "rack_kind", None)))
    task_rack_code = coerce_optional_str(getattr(task, "rack_code", None))
    if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value and task_rack_kind is None:
        return False
    if task_type == RackTaskType.MOVE_RACK.value and task_rack_code is None:
        return False
    placement_rack_code = coerce_optional_str(getattr(placement, "rack_code", None))
    if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value and placement_rack_code in move_out_rack_codes:
        return False
    if task_rack_code is not None and placement_rack_code != task_rack_code:
        return False
    placement_rack_kind = coerce_optional_str(enum_value(getattr(placement, "rack_kind", None)))
    return task_rack_kind is None or placement_rack_kind == task_rack_kind


def _task_required(task: Any) -> bool:
    actions_json = getattr(task, "actions_json", None)
    if isinstance(actions_json, dict) and "required" in actions_json:
        return bool(actions_json["required"])
    return True


def _task_type(task: Any) -> str | None:
    return enum_value(getattr(task, "task_type", None))


rack_operation_service = RackOperationService()


__all__ = [
    "DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS",
    "RackOperationMigrationRequiredError",
    "RackOperationService",
    "RackOperationStatus",
    "RackTaskSpec",
    "rack_operation_service",
]
