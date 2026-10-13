from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from manual_picking.application.return_rack_task_completion import ManualPickingReturnRackTaskCompletion

from src.app.execution.plugin_binding import BusinessEvidenceDisposition
from src.app.wms_integration.return_rack_task_completion.types import ReturnRackTaskCompletedFact

FACT = ReturnRackTaskCompletedFact(91, 31, "PICK-1", "R1", 2, 1788390199000, "019f3404-a100-7b01-8b01-000000000099")


@pytest.mark.asyncio
async def test_whole_completion_waits_only_for_plan_without_faking_faces():
    task = SimpleNamespace(id=31, workline_id=7, last_applied_plan_revision=1)
    store = SimpleNamespace(
        get=AsyncMock(return_value=None),
        members=AsyncMock(return_value=(1,)),
        add=AsyncMock(),
    )
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task)), completions=store
    )
    assert (await app.apply_in_session(object(), FACT, 7)).disposition == BusinessEvidenceDisposition.DEFERRED
    store.members.assert_not_awaited()
    task.last_applied_plan_revision = 2
    assert (await app.apply_in_session(object(), FACT, 7)).disposition == BusinessEvidenceDisposition.APPLIED
    store.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_same_rack_members_are_conflict_not_silent_success():
    task = SimpleNamespace(id=31, workline_id=7, last_applied_plan_revision=3)
    store = SimpleNamespace(
        get=AsyncMock(return_value=None),
        members=AsyncMock(return_value=(3,)),
        add=AsyncMock(),
    )
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task)), completions=store
    )
    assert (await app.apply_in_session(object(), FACT, 7)).disposition == BusinessEvidenceDisposition.RECONCILING
    store.add.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("task", [None, SimpleNamespace(id=32, workline_id=7), SimpleNamespace(id=31, workline_id=8)])
async def test_completion_rejects_task_or_workline_identity_mismatch(task):
    store = SimpleNamespace(get=AsyncMock(), members=AsyncMock(), add=AsyncMock())
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task)), completions=store
    )
    assert (await app.apply_in_session(object(), FACT, 7)).disposition == BusinessEvidenceDisposition.RECONCILING
    store.get.assert_not_awaited()
    store.add.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [None, "operation_id", "through_plan_revision", "completed_at_ms"])
async def test_existing_marker_replay_preserves_frozen_identity(changed):
    marker = SimpleNamespace(
        operation_id=FACT.operation_id,
        through_plan_revision=FACT.through_plan_revision,
        completed_at_ms=FACT.completed_at_ms,
    )
    if changed is not None:
        setattr(marker, changed, "other" if changed == "operation_id" else getattr(marker, changed) + 1)
    store = SimpleNamespace(get=AsyncMock(return_value=marker), members=AsyncMock(), add=AsyncMock())
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(
            get_by_task_id_for_update=AsyncMock(
                return_value=SimpleNamespace(id=31, workline_id=7, last_applied_plan_revision=2)
            )
        ),
        completions=store,
    )
    result = await app.apply_in_session(object(), FACT, 7)
    assert result.disposition == (
        BusinessEvidenceDisposition.APPLIED if changed is None else BusinessEvidenceDisposition.RECONCILING
    )
    assert bool(result.decision_digest) is (changed is None)
    store.members.assert_not_awaited()
    store.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_without_rack_members_cannot_create_marker():
    store = SimpleNamespace(get=AsyncMock(return_value=None), members=AsyncMock(return_value=()), add=AsyncMock())
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(
            get_by_task_id_for_update=AsyncMock(
                return_value=SimpleNamespace(id=31, workline_id=7, last_applied_plan_revision=2)
            )
        ),
        completions=store,
    )
    assert (await app.apply_in_session(object(), FACT, 7)).disposition == BusinessEvidenceDisposition.RECONCILING
    store.add.assert_not_awaited()
