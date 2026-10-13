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
async def test_completion_applies_covered_members_with_later_same_rack_members():
    task = SimpleNamespace(id=31, workline_id=7, last_applied_plan_revision=3)
    store = SimpleNamespace(
        get=AsyncMock(return_value=None),
        members=AsyncMock(return_value=(1, 3)),
        add=AsyncMock(),
    )
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task)), completions=store
    )
    assert (await app.apply_in_session(object(), FACT, 7)).disposition == BusinessEvidenceDisposition.APPLIED
    store.add.assert_awaited_once()


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
@pytest.mark.parametrize("revision", [1, 2, 3])
async def test_completion_advances_cutoff_and_older_notification_does_not_regress_it(revision):
    marker = SimpleNamespace(
        operation_id="another-operation",
        through_plan_revision=revision,
        completed_at_ms=FACT.completed_at_ms - 1,
    )
    store = SimpleNamespace(get=AsyncMock(return_value=marker), members=AsyncMock(return_value=(1, 3)), add=AsyncMock())
    app = ManualPickingReturnRackTaskCompletion(
        tasks=SimpleNamespace(
            get_by_task_id_for_update=AsyncMock(
                return_value=SimpleNamespace(id=31, workline_id=7, last_applied_plan_revision=3)
            )
        ),
        completions=store,
    )
    result = await app.apply_in_session(object(), FACT, 7)
    assert result.disposition == BusinessEvidenceDisposition.APPLIED
    assert result.decision_digest
    if revision < FACT.through_plan_revision:
        store.add.assert_awaited_once()
    else:
        store.members.assert_not_awaited()
        store.add.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("members", [(), (3,)])
async def test_completion_without_covered_rack_members_cannot_create_marker(members):
    store = SimpleNamespace(get=AsyncMock(return_value=None), members=AsyncMock(return_value=members), add=AsyncMock())
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
