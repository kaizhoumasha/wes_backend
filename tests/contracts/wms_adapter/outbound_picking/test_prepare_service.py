from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareRuntimeFacts, PrepareTaskType

from src.app.execution.models import WmsConfirmation
from src.app.execution.services import WmsConfirmationAcceptance
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import (
    PickingTaskPrepareCoordinator,
    PickingTaskPrepareNoopReason,
)
from src.app.workline.models import LineType, WorkLineRunMode


class _Sessions:
    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield object()


class _Worklines:
    def __init__(self, workline, order: list[str]):  # type: ignore[no-untyped-def]
        self.workline = workline
        self.calls: list[str] = []
        self.order = order

    async def get_for_update(self, _db: object, _workline_id: int):  # type: ignore[no-untyped-def]
        self.calls.append("lock_workline")
        self.order.append("lock_workline")
        return self.workline

    async def get_unfinished_workload_summary(self, _db: object, _workline_id: int):  # type: ignore[no-untyped-def]
        return {
            "count": 0,
            "sample": None,
            "by_type": {
                "material_executions": False,
                "device_commands": False,
                "transport_tasks": False,
                "inbound_evidences": False,
                "wms_confirmations": False,
            },
        }


class _Policy:
    def select_task_type(self, context: PrepareContext) -> PrepareTaskType | None:
        return PrepareTaskType.MANUAL if context.is_active else None

    def is_ready(self, facts: PrepareRuntimeFacts, *, now: datetime) -> bool:
        return not facts.has_active_incident


class _Facts:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def read_facts(self, _db: object, *, workline_id: int) -> PrepareRuntimeFacts:
        return PrepareRuntimeFacts(not self.ready, True, (), False)


class _Tasks:
    def __init__(self, task: PickingTask | None) -> None:
        self.task = task
        self.flushed = False
        self.active = False
        self.claimed_type = None

    async def has_active_for_workline(self, _db: object, _workline_id: int) -> bool:
        return self.active

    async def claim_next_queued(self, _db: object, *, task_type: PickingTaskType, now_ms: int) -> PickingTask | None:
        self.claimed_type = task_type
        return self.task

    async def flush(self, _db: object) -> None:
        self.flushed = True


class _Confirmations:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    async def create_or_get(self, _db: object, **kwargs: object) -> WmsConfirmationAcceptance:
        self.kwargs = kwargs
        confirmation = WmsConfirmation(
            operation=str(kwargs["operation"]),
            operation_id=str(kwargs["operation_id"]),
            picking_task_id=int(kwargs["picking_task_id"]),
            request_digest="a" * 64,
            request_payload=kwargs["request_payload"],  # type: ignore[arg-type]
            deadline_at=kwargs["deadline_at"],  # type: ignore[arg-type]
            created_at=kwargs["created_at"],  # type: ignore[arg-type]
        )
        confirmation.id = 41
        return WmsConfirmationAcceptance(confirmation, duplicate=False)


class _Queue:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def enqueue_wms_confirmations(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("broker unavailable")


def _task() -> PickingTask:
    task = PickingTask(
        task_id="PICK-1",
        task_type=PickingTaskType.MANUAL,
        queue_revision=1,
        dispatch_sequence=10,
        issued_at_ms=1,
        issued_evidence_id=11,
    )
    task.id = 31
    return task


def _service(
    *,
    task: PickingTask | None = None,
    workline: object | None = None,
    ready: bool = True,
    queue: _Queue | None = None,
    policy: _Policy | None = None,
) -> tuple[PickingTaskPrepareCoordinator, _Worklines, _Tasks, _Confirmations, _Queue]:
    workline_value = (
        workline
        if workline is not None
        else SimpleNamespace(
            id=7,
            line_code="LINE-1",
            plugin_key="manual_bin_processing",
            flow_mode="MANUAL_BIN_PROCESSING",
            is_active=True,
            line_type=LineType.MANUAL,
            run_mode=WorkLineRunMode.AUTO,
        )
    )
    lock_order: list[str] = []
    worklines = _Worklines(workline_value, lock_order)
    tasks = _Tasks(task if task is not None else _task())
    confirmations = _Confirmations()
    queue_value = queue or _Queue()
    return (
        PickingTaskPrepareCoordinator(
            _Sessions(),  # type: ignore[arg-type]
            policy=policy or _Policy(),
            workline_repository=worklines,  # type: ignore[arg-type]
            facts_repository=_Facts(ready),  # type: ignore[arg-type]
            task_repository=tasks,  # type: ignore[arg-type]
            confirmation_service=confirmations,  # type: ignore[arg-type]
            task_queue_gateway=queue_value,  # type: ignore[arg-type]
        ),
        worklines,
        tasks,
        confirmations,
        queue_value,
    )


@pytest.mark.asyncio
async def test_prepare_claims_one_manual_task_and_creates_confirmation_in_lock_order() -> None:
    service, worklines, tasks, confirmations, queue = _service()

    result = await service.prepare_next_for_workline(7, now=datetime(2026, 9, 4))

    assert result.prepared is True
    assert result.reason is None
    assert result.task is tasks.task
    assert tasks.task is not None
    assert PickingTaskStatus(tasks.task.status) is PickingTaskStatus.PREPARING
    assert tasks.task.workline_id == 7
    assert tasks.flushed is True
    assert tasks.claimed_type is PickingTaskType.MANUAL
    assert worklines.calls == ["lock_workline"]
    assert worklines.order == ["lock_workline"]
    assert confirmations.kwargs is not None
    assert confirmations.kwargs["picking_task_id"] == 31
    assert confirmations.kwargs["request_payload"]["data"] == {  # type: ignore[index]
        "task_id": "PICK-1",
        "workline_code": "LINE-1",
    }
    assert queue.calls == 1


@pytest.mark.asyncio
async def test_prepare_expected_task_fails_closed_before_mutating_queue_head() -> None:
    service, _worklines, tasks, confirmations, queue = _service()

    result = await service.prepare_next_for_workline(
        7,
        expected_task_id="PICK-OTHER",
        now=datetime(2026, 9, 4),
    )

    assert result.prepared is False
    assert result.reason is PickingTaskPrepareNoopReason.SELECTED_TASK_NOT_NEXT
    assert tasks.task is not None
    assert PickingTaskStatus(tasks.task.status) is PickingTaskStatus.QUEUED
    assert tasks.task.workline_id is None
    assert tasks.flushed is False
    assert confirmations.kwargs is None
    assert queue.calls == 0


@pytest.mark.asyncio
async def test_prepare_keeps_persisted_obligation_when_immediate_enqueue_fails() -> None:
    queue = _Queue(fail=True)
    service, _worklines, tasks, _confirmations, _queue = _service(queue=queue)

    result = await service.prepare_next_for_workline(7, now=datetime(2026, 9, 4))

    assert result.prepared is True
    assert tasks.task is not None and PickingTaskStatus(tasks.task.status) is PickingTaskStatus.PREPARING
    assert queue.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"workline": SimpleNamespace(id=7, is_active=False)},
            PickingTaskPrepareNoopReason.WORKLINE_NOT_READY,
        ),
        ({"ready": False}, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY),
        ({"task": None}, PickingTaskPrepareNoopReason.NO_ELIGIBLE_TASK),
    ],
)
async def test_prepare_noop_never_creates_confirmation_or_enqueues(
    overrides: dict[str, object], reason: PickingTaskPrepareNoopReason
) -> None:
    service, _worklines, tasks, confirmations, queue = _service(**overrides)  # type: ignore[arg-type]
    if "task" in overrides and overrides["task"] is None:
        tasks.task = None

    result = await service.prepare_next_for_workline(7, now=datetime(2026, 9, 4))

    assert result.prepared is False
    assert result.reason is reason
    assert confirmations.kwargs is None
    assert queue.calls == 0


@pytest.mark.asyncio
async def test_missing_workline_cannot_claim_or_create_prepare_obligation() -> None:
    service, worklines, tasks, confirmations, queue = _service()
    worklines.workline = None
    result = await service.prepare_next_for_workline(7, now=datetime(2026, 9, 4))
    assert result.reason is PickingTaskPrepareNoopReason.WORKLINE_NOT_READY
    assert tasks.claimed_type is None
    assert confirmations.kwargs is None
    assert queue.calls == 0


def test_prepare_requires_explicit_policy():
    with pytest.raises(TypeError, match="policy"):
        PickingTaskPrepareCoordinator(_Sessions(), task_queue_gateway=_Queue())


@pytest.mark.asyncio
async def test_coordinator_uses_injected_policy_without_manual_context_rules() -> None:
    class AutoPolicy(_Policy):
        def select_task_type(self, context: PrepareContext) -> PrepareTaskType:
            assert context.plugin_key == "another_plugin"
            return PrepareTaskType.AUTO

    service, _, tasks, _, _ = _service(
        policy=AutoPolicy(),
        workline=SimpleNamespace(
            id=7,
            line_code="LINE-1",
            is_active=True,
            line_type="AUTO",
            run_mode="AUTO",
            plugin_key="another_plugin",
            flow_mode="OTHER",
        ),
    )
    tasks.task.task_type = PickingTaskType.AUTO
    result = await service.prepare_next_for_workline(7, now=datetime(2026, 9, 4))
    assert result.prepared
    assert tasks.claimed_type is PickingTaskType.AUTO
