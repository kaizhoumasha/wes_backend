"""人工拣料完成确认只结束业务任务，保留退箱物理义务。"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.completion_flow import ManualPickingCompletionFlow


class _Repository:
    def __init__(self) -> None:
        self.ready = True
        self.latest = None

    async def ready_to_confirm(self, _db, _line, _task):  # type: ignore[no-untyped-def]
        return self.ready

    async def latest_confirmation(self, _db, _task_id):  # type: ignore[no-untyped-def]
        return self.latest


class _Scheduler:
    def __init__(self) -> None:
        self.intents = []

    async def create_in_session(self, _db, intent, *, picking_task_id, created_at):  # type: ignore[no-untyped-def]
        self.intents.append((intent, picking_task_id, created_at))


@pytest.mark.asyncio
async def test_completion_waits_for_local_work_then_creates_one_typed_obligation() -> None:
    repository = _Repository()
    scheduler = _Scheduler()
    flow = ManualPickingCompletionFlow(repository, scheduler, uuid_factory=lambda: "op-1")
    task = SimpleNamespace(
        id=11, task_id="PICK-1", status="EXECUTING", last_applied_plan_revision=2, plan_blocked_evidence_id=None
    )
    line = SimpleNamespace(id=7)
    now = datetime(2026, 9, 14, 4)
    repository.ready = False
    assert await flow.advance_in_session(object(), line, task, now=now) == 0
    repository.ready = True
    assert await flow.advance_in_session(object(), line, task, now=now) == 1
    assert scheduler.intents == [
        (sdk.CompletionConfirmIntent(operation_id="op-1", task_id="PICK-1", last_applied_plan_revision=2), 11, now)
    ]


@pytest.mark.asyncio
async def test_completed_result_closes_only_task_without_creating_another_request() -> None:
    repository = _Repository()
    scheduler = _Scheduler()
    flow = ManualPickingCompletionFlow(repository, scheduler, uuid_factory=lambda: "op-2")
    task = SimpleNamespace(
        id=11, task_id="PICK-1", status="EXECUTING", last_applied_plan_revision=2, plan_blocked_evidence_id=None
    )
    repository.latest = SimpleNamespace(
        status="COMPLETED",
        task_id="PICK-1",
        outcome=sdk.CompletionConfirmOutcome(sdk.PickingTaskCompleted()),
        plan_revision=2,
        completed_at=datetime(2026, 9, 14, 4),
    )
    assert await flow.advance_in_session(object(), SimpleNamespace(id=7), task, now=datetime(2026, 9, 14, 4)) == 1
    assert task.status == "EXECUTION_COMPLETED"
    assert scheduler.intents == []


@pytest.mark.asyncio
async def test_business_wait_retries_with_new_identity_only_when_due_and_stale_waits_for_delta() -> None:
    repository = _Repository()
    scheduler = _Scheduler()
    flow = ManualPickingCompletionFlow(repository, scheduler, uuid_factory=lambda: "op-next")
    task = SimpleNamespace(
        id=11, task_id="PICK-1", status="EXECUTING", last_applied_plan_revision=2, plan_blocked_evidence_id=None
    )
    completed_at = datetime(2026, 9, 14, 4)
    repository.latest = SimpleNamespace(
        status="COMPLETED",
        task_id="PICK-1",
        outcome=sdk.CompletionConfirmOutcome(sdk.PickingTaskBusinessInProgress(1000)),
        plan_revision=2,
        completed_at=completed_at,
    )
    assert await flow.advance_in_session(object(), SimpleNamespace(id=7), task, now=completed_at) == 0
    assert (
        await flow.advance_in_session(object(), SimpleNamespace(id=7), task, now=completed_at + timedelta(seconds=1))
        == 1
    )
    assert scheduler.intents[0][0].operation_id == "op-next"
    scheduler.intents.clear()
    repository.latest = SimpleNamespace(
        status="COMPLETED",
        task_id="PICK-1",
        outcome=sdk.CompletionConfirmOutcome(sdk.PickingTaskPlanRevisionStale(3)),
        plan_revision=2,
        completed_at=completed_at,
    )
    assert (
        await flow.advance_in_session(object(), SimpleNamespace(id=7), task, now=completed_at + timedelta(seconds=1))
        == 0
    )
    task.last_applied_plan_revision = 3
    assert (
        await flow.advance_in_session(object(), SimpleNamespace(id=7), task, now=completed_at + timedelta(seconds=1))
        == 1
    )
    assert scheduler.intents[0][0].last_applied_plan_revision == 3


@pytest.mark.asyncio
async def test_unknown_confirmation_never_changes_identity_or_task_status() -> None:
    repository = _Repository()
    scheduler = _Scheduler()
    flow = ManualPickingCompletionFlow(repository, scheduler, uuid_factory=lambda: "op-next")
    task = SimpleNamespace(
        id=11, task_id="PICK-1", status="EXECUTING", last_applied_plan_revision=2, plan_blocked_evidence_id=None
    )
    repository.latest = SimpleNamespace(
        status="RECONCILING", task_id="PICK-1", outcome=None, plan_revision=2, completed_at=None
    )
    assert await flow.advance_in_session(object(), SimpleNamespace(id=7), task, now=datetime(2026, 9, 14, 4)) == 0
    assert task.status == "EXECUTING"
    assert scheduler.intents == []
