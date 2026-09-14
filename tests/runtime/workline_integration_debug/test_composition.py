from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline_integration_debug.composition import CombinedWorkLineConfirmationOwner
from src.app.workline_integration_debug.service import IntegrationRunWorkLineOwner


@pytest.mark.asyncio
async def test_active_debug_run_reserves_only_its_workline() -> None:
    repository = AsyncMock()
    repository.get_active_for_workline.side_effect = [SimpleNamespace(run_id="run-1"), None]
    repository.owns_operation.return_value = True
    owner = IntegrationRunWorkLineOwner(repository)
    db = object()

    assert await owner.is_reserved(db, 3) is True  # type: ignore[arg-type]
    assert await owner.is_reserved(db, 4) is False  # type: ignore[arg-type]
    assert repository.get_active_for_workline.await_args_list[0].args == (db, 3)
    assert repository.get_active_for_workline.await_args_list[1].args == (db, 4)
    assert await owner.owns_operation(db, 3, "op-1") is True  # type: ignore[arg-type]
    repository.owns_operation.assert_awaited_once_with(db, workline_id=3, operation_id="op-1")


@pytest.mark.asyncio
async def test_combined_owner_checks_debug_identity_after_existing_owner_declines() -> None:
    existing = AsyncMock()
    existing.validate_owner.return_value = False
    debug = AsyncMock()
    debug.validate_owner.return_value = True
    owner = CombinedWorkLineConfirmationOwner(existing, debug)
    payload = {"operation": "outbound.manual_bin.work_admission_decide@v1", "operation_id": "op-1"}

    assert await owner.validate_owner(object(), workline_id=3, request_payload=payload) is True  # type: ignore[arg-type]
    existing.validate_owner.assert_awaited_once()
    debug.validate_owner.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_task_id", "run_bin_code", "expected"),
    [
        (None, None, False),
        ("PICK-001", "A000000001", True),
        ("PICK-OTHER", "A000000001", False),
        ("PICK-001", "A000000002", False),
    ],
)
async def test_completion_owner_requires_exact_active_run_identity(
    run_task_id: str | None, run_bin_code: str | None, expected: bool
) -> None:
    repository = AsyncMock()
    repository.get_active_for_workline.return_value = (
        SimpleNamespace(task_id=run_task_id, bin_code=run_bin_code) if run_task_id is not None else None
    )
    owner = IntegrationRunWorkLineOwner(repository)
    db = object()

    assert (
        await owner.owns_completion(
            db,
            workline_id=3,
            task_id="PICK-001",
            bin_code="A000000001",  # type: ignore[arg-type]
        )
        is expected
    )
    repository.get_active_for_workline.assert_awaited_once_with(db, 3, for_update=True)
