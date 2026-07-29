"""旧 Rack operation producer 的 fail-closed 回归。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.rack.models import RackOperationStatus, RackTaskStatus, RackTaskType
from src.app.rack.services import RackOperationMigrationRequiredError, RackOperationService
from src.app.resource.models import RackKind
from src.app.sys.models import OperationCompletionPolicy


class _RackTaskReadRepository:
    def __init__(self, tasks: list[SimpleNamespace]) -> None:
        self.tasks = tasks

    async def list_by_operation_key(self, _db: Any, *, operation_key: str) -> list[SimpleNamespace]:
        return [task for task in self.tasks if task.operation_key == operation_key]


class _RackOperationReadRepository:
    def __init__(self, *, operation_key: str, completion_policy: OperationCompletionPolicy) -> None:
        self.operation = SimpleNamespace(
            operation_key=operation_key,
            completion_policy=completion_policy,
            result_json={},
        )
        self.mark_calls: list[dict[str, Any]] = []

    async def get_by_operation_key(self, _db: Any, operation_key: str) -> SimpleNamespace | None:
        return self.operation if operation_key == self.operation.operation_key else None

    async def mark_status(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.mark_calls.append(dict(kwargs))
        self.operation.operation_status = kwargs["operation_status"]
        self.operation.result_json.update(kwargs["result_json_patch"])
        return self.operation


class _RackPlacementReadRepository:
    def __init__(self, placements_by_position: dict[str, list[SimpleNamespace]] | None = None) -> None:
        self.placements_by_position = placements_by_position or {}

    async def list_active_by_workline_position(
        self,
        _db: Any,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[SimpleNamespace]:
        del workline_code
        return list(self.placements_by_position.get(position_code, ()))


def _task(
    *,
    operation_key: str,
    task_type: RackTaskType,
    task_status: RackTaskStatus,
    rack_code: str | None = None,
    rack_kind: RackKind = RackKind.SINGLE_LAYER,
    source_position_code: str | None = None,
    target_position_code: str | None = "CLASSIFIER-WORK",
) -> SimpleNamespace:
    return SimpleNamespace(
        operation_key=operation_key,
        task_type=task_type,
        task_status=task_status,
        rack_code=rack_code,
        rack_kind=rack_kind,
        source_position_code=source_position_code,
        target_position_code=target_position_code,
        workline_code="WL-SMT-01",
        actions_json={"required": True},
    )


def _placement(rack_code: str, rack_kind: RackKind = RackKind.SINGLE_LAYER) -> SimpleNamespace:
    return SimpleNamespace(rack_code=rack_code, rack_kind=rack_kind)


def _read_service(
    *,
    operation_key: str,
    tasks: list[SimpleNamespace],
    completion_policy: OperationCompletionPolicy = OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED,
    placements_by_position: dict[str, list[SimpleNamespace]] | None = None,
) -> tuple[RackOperationService, _RackOperationReadRepository, _RackPlacementReadRepository]:
    operation_repository = _RackOperationReadRepository(
        operation_key=operation_key,
        completion_policy=completion_policy,
    )
    placement_repository = _RackPlacementReadRepository(placements_by_position)
    return (
        RackOperationService(
            rack_task_repository=_RackTaskReadRepository(tasks),
            rack_operation_repository=operation_repository,
            rack_placement_repository=placement_repository,
        ),
        operation_repository,
        placement_repository,
    )


@pytest.mark.asyncio
async def test_rack_operation_rejects_before_persisting_legacy_transport() -> None:
    with pytest.raises(RackOperationMigrationRequiredError, match="T5 dispatcher is not implemented"):
        await RackOperationService().request_operation_tasks(
            None,
            operation_key="rack-removed-001",
            operation_type="RACK_TRANSPORT",
            session=None,
            trace_id="trace-rack-removed",
            task_specs=[],
        )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_all_required_tasks_succeeded() -> None:
    operation_key = "op-required-tasks"
    tasks = [
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
            rack_code="RACK-OLD",
            source_position_code="SOURCE",
            target_position_code=None,
        ),
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
            task_status=RackTaskStatus.REQUESTED,
        ),
    ]
    service, _operations, placements = _read_service(
        operation_key=operation_key,
        tasks=tasks,
        placements_by_position={"SOURCE": [], "CLASSIFIER-WORK": [_placement("RACK-NEW")]},
    )

    assert await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.PENDING.value

    tasks[1].task_status = RackTaskStatus.SUCCEEDED
    assert (
        await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.SUCCEEDED.value
    )
    assert placements.placements_by_position["CLASSIFIER-WORK"][0].rack_code == "RACK-NEW"


@pytest.mark.asyncio
async def test_derive_operation_status_requires_resource_projection_confirmation() -> None:
    operation_key = "op-projection"
    tasks = [
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
        )
    ]
    service, _operations, placements = _read_service(operation_key=operation_key, tasks=tasks)

    assert (
        await service.derive_operation_status(None, operation_key=operation_key)
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [_placement("RACK-NEW")]
    assert (
        await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_consumes_projection_per_inbound_task() -> None:
    operation_key = "op-projection-count"
    tasks = [
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
        ),
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
        ),
    ]
    service, _operations, placements = _read_service(
        operation_key=operation_key,
        tasks=tasks,
        placements_by_position={"CLASSIFIER-WORK": [_placement("RACK-NEW-1")]},
    )

    assert (
        await service.derive_operation_status(None, operation_key=operation_key)
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"].append(_placement("RACK-NEW-2"))
    assert (
        await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_reconciles_when_move_out_rack_still_at_source_position() -> None:
    operation_key = "op-source-not-cleared"
    tasks = [
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
            rack_code="RACK-OLD",
            source_position_code="CLASSIFIER-WORK",
            target_position_code=None,
        ),
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
        ),
    ]
    service, _operations, placements = _read_service(
        operation_key=operation_key,
        tasks=tasks,
        placements_by_position={"CLASSIFIER-WORK": [_placement("RACK-OLD")]},
    )

    assert (
        await service.derive_operation_status(None, operation_key=operation_key)
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [_placement("RACK-NEW")]
    assert (
        await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_matching_rack_kind_projection() -> None:
    operation_key = "op-rack-kind"
    tasks = [
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
            rack_kind=RackKind.SINGLE_LAYER,
        )
    ]
    service, _operations, placements = _read_service(
        operation_key=operation_key,
        tasks=tasks,
        placements_by_position={"CLASSIFIER-WORK": [_placement("RACK-FIVE", RackKind.FIVE_LAYER)]},
    )

    assert (
        await service.derive_operation_status(None, operation_key=operation_key)
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["CLASSIFIER-WORK"] = [_placement("RACK-SINGLE", RackKind.SINGLE_LAYER)]
    assert (
        await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.SUCCEEDED.value
    )


@pytest.mark.asyncio
async def test_derive_operation_status_requires_move_rack_target_projection() -> None:
    operation_key = "op-move-rack-target"
    tasks = [
        _task(
            operation_key=operation_key,
            task_type=RackTaskType.MOVE_RACK,
            task_status=RackTaskStatus.SUCCEEDED,
            rack_code="RACK-MOVED",
            source_position_code=None,
            target_position_code="TARGET",
        )
    ]
    service, _operations, placements = _read_service(
        operation_key=operation_key,
        tasks=tasks,
        placements_by_position={"TARGET": [_placement("RACK-OTHER")]},
    )

    assert (
        await service.derive_operation_status(None, operation_key=operation_key)
        == RackOperationStatus.RECONCILING.value
    )

    placements.placements_by_position["TARGET"] = [_placement("RACK-MOVED")]
    assert (
        await service.derive_operation_status(None, operation_key=operation_key) == RackOperationStatus.SUCCEEDED.value
    )
