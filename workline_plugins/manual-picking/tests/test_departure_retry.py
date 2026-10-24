"""清退拒绝重求值共用持久指数退避，不能因新决定身份或扫描重启加速。"""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from departure_retry_support import RetrySession
from manual_picking.application.batch_driver import TRANSFER_RACK_OUT_STEP, ManualPickingBatchDriver
from manual_picking.application.drain_repository import (
    DRAIN_RACK_OUT_STEP,
    RETIRED_DRAIN_RACK_OUT_STEP,
    RETURN_RACK_OUT_STEP,
    SOURCE_RACK_OUT_STEP,
)

NOW = datetime(2026, 10, 24, 0, 0)
STEPS = (
    SOURCE_RACK_OUT_STEP,
    DRAIN_RACK_OUT_STEP,
    RETIRED_DRAIN_RACK_OUT_STEP,
    RETURN_RACK_OUT_STEP,
    TRANSFER_RACK_OUT_STEP,
)


def context(step):
    parent = SimpleNamespace(id=31, task_id="task-1", status="EXECUTING")
    execution = SimpleNamespace(status="EXCEPTION_RETIRED" if step == RETIRED_DRAIN_RACK_OUT_STEP else "ACTIVE")
    scheduler = SimpleNamespace(create_in_session=AsyncMock())
    driver = ManualPickingBatchDriver(
        None,
        plans=None,
        positions=None,
        transports=None,
        rack_creator=None,
        departure_scheduler=scheduler,
        departure_reader=None,
        tasks=SimpleNamespace(get_by_id_for_update=AsyncMock(return_value=parent)),
        drain=SimpleNamespace(
            repository=SimpleNamespace(executions=SimpleNamespace(for_evidence=AsyncMock(return_value=execution)))
        ),
    )
    binding = SimpleNamespace(
        picking_task_id=None if step in (DRAIN_RACK_OUT_STEP, RETIRED_DRAIN_RACK_OUT_STEP) else parent.id,
        source_evidence_id=99,
        causal_token=1,
        correlation_id="departure:decision-1",
    )
    task = SimpleNamespace(
        transport_task_id="transport-1",
        status="REJECTED",
        outcome_version=1,
        published_outcome_version=1,
        submit_operation_id="frozen-operation",
        submit_request_body="frozen-body",
    )
    line = SimpleNamespace(id=7)
    kwargs = {
        "scope_identity": "arrival:ingress-1",
        "rack_id": "rack-1",
        "step": step,
        "location": "KT18",
        "face": "90",
    }
    if step == TRANSFER_RACK_OUT_STEP:
        kwargs.update(scope_identity="task:31:rack:rack-1", picking_task=parent)
    return driver, scheduler, parent, execution, binding, task, line, kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("step", STEPS)
async def test_redecision_backoff_survives_new_decision_identity_and_driver_restart(step):
    driver, scheduler, _, _, binding, task, line, kwargs = context(step)
    db = RetrySession()
    now = NOW
    operation_ids = []
    for attempt, seconds in enumerate((1, 2, 4, 8, 16, 32, 60, 60), 1):
        binding.causal_token = attempt
        binding.correlation_id = f"departure:new-wms-decision-{attempt}"
        task.transport_task_id = f"transport-{attempt}"
        frozen = deepcopy(vars(task))
        assert await driver._redecide_rejected_departure(db, line, (binding, task), now=now, **kwargs) == 0
        cursor = next(iter(db.rows.values()))
        assert cursor.next_attempt_at == now + timedelta(seconds=seconds)
        for tick in range(20):
            assert (
                await driver._redecide_rejected_departure(
                    db, line, (binding, task), now=now + timedelta(milliseconds=tick), **kwargs
                )
                == 0
            )
        assert scheduler.create_in_session.await_count == attempt - 1
        due = now + timedelta(seconds=seconds)
        assert await driver._redecide_rejected_departure(db, line, (binding, task), now=due, **kwargs) == 1
        operation_ids.append(scheduler.create_in_session.await_args.args[1].operation_id)
        assert cursor.decision_operation_id == operation_ids[-1]
        # A lagging latest-decision read must not create another operation after restart.
        restarted, _, _, _, _, _, _, _ = context(step)
        restarted._departure_scheduler = scheduler
        for tick in range(20):
            assert (
                await restarted._redecide_rejected_departure(
                    db, line, (binding, task), now=due + timedelta(milliseconds=tick), **kwargs
                )
                == 0
            )
        assert scheduler.create_in_session.await_count == attempt
        assert vars(task) == frozen
        now = due + timedelta(seconds=1)
        driver = restarted
    assert len(set(operation_ids)) == 8
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_new_arrival_round_does_not_inherit_old_departure_cooldown():
    driver, _, _, _, binding, task, line, kwargs = context(SOURCE_RACK_OUT_STEP)
    db = RetrySession()
    binding.correlation_id = "retry:20:previous"
    assert await driver._redecide_rejected_departure(db, line, (binding, task), now=NOW, **kwargs) == 0
    assert next(iter(db.rows.values())).next_attempt_at == NOW + timedelta(seconds=60)
    kwargs["scope_identity"] = "arrival:new-ingress"
    binding.correlation_id = "departure:new-round"
    binding.causal_token = 2
    task.transport_task_id = "new-round-rejection"
    assert await driver._redecide_rejected_departure(db, line, (binding, task), now=NOW, **kwargs) == 0
    assert sorted(row.next_attempt_at for row in db.rows.values()) == [
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=60),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawn", ("ARCHIVED", "MANUAL_WITHDRAWN", "UNPUBLISHED"))
async def test_withdrawn_or_unpublished_departure_never_creates_retry_cursor(withdrawn):
    step = DRAIN_RACK_OUT_STEP if withdrawn == "MANUAL_WITHDRAWN" else SOURCE_RACK_OUT_STEP
    driver, scheduler, parent, execution, binding, task, line, kwargs = context(step)
    if withdrawn == "ARCHIVED":
        parent.status = "ARCHIVED"
    elif withdrawn == "MANUAL_WITHDRAWN":
        execution.status = "MANUAL_WITHDRAWN"
    else:
        task.published_outcome_version = 0
    db = RetrySession()
    assert await driver._redecide_rejected_departure(db, line, (binding, task), now=NOW, **kwargs) == 0
    assert db.rows == {}
    scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_older_rejection_cannot_reset_current_cursor():
    driver, scheduler, _, _, binding, task, line, kwargs = context(SOURCE_RACK_OUT_STEP)
    db = RetrySession()
    binding.causal_token = 2
    assert await driver._redecide_rejected_departure(db, line, (binding, task), now=NOW, **kwargs) == 0
    row = next(iter(db.rows.values()))
    before = (row.last_transport_task_id, row.next_attempt_at, row.retry_count)
    binding.causal_token = 1
    task.transport_task_id = "older-task"
    assert (
        await driver._redecide_rejected_departure(db, line, (binding, task), now=NOW + timedelta(days=1), **kwargs) == 0
    )
    assert (row.last_transport_task_id, row.next_attempt_at, row.retry_count) == before
    scheduler.create_in_session.assert_not_awaited()
