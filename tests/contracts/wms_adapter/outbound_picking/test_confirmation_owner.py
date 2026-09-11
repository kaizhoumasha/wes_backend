"""PickingTask operation 的确认 owner 边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.wms_integration.outbound_picking.services.picking_task_confirmation_owner import (
    PickingTaskConfirmationOwnerService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,accepted", [("QUEUED", False), ("PREPARING", True), ("EXECUTING", True), ("EXECUTION_COMPLETED", True)]
)
async def test_arrival_obligation_survives_business_progress(state, accepted):
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(status=state, workline_id=1))
    )
    service = PickingTaskConfirmationOwnerService(repository)
    assert (
        await service.validate_response_owner(
            object(), picking_task_id=1, operation="outbound.return_rack.arrival_report@v1"
        )
        is accepted
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,accepted", [("QUEUED", False), ("PREPARING", True), ("EXECUTING", False), ("EXECUTION_COMPLETED", False)]
)
async def test_prepare_retains_existing_owner_state_contract(state, accepted):
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(status=state, workline_id=1))
    )
    service = PickingTaskConfirmationOwnerService(repository)
    assert (
        await service.validate_response_owner(object(), picking_task_id=1, operation="outbound.picking_task.prepare@v1")
        is accepted
    )


@pytest.mark.asyncio
async def test_unknown_operation_does_not_query_owner():
    repository = SimpleNamespace(get_by_id_for_update=AsyncMock())
    assert not await PickingTaskConfirmationOwnerService(repository).validate_response_owner(
        object(), picking_task_id=1, operation="unknown@v1"
    )
    repository.get_by_id_for_update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,accepted",
    [
        ("QUEUED", False),
        ("PREPARING", False),
        ("EXECUTING", True),
        ("EXECUTION_COMPLETED", True),
    ],
)
async def test_departure_remains_available_after_business_completion(state, accepted):
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(status=state, workline_id=1))
    )
    assert (
        await PickingTaskConfirmationOwnerService(repository).validate_response_owner(
            object(), picking_task_id=1, operation="outbound.rack.departure_decide@v1"
        )
        is accepted
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "outbound.bin.inbound_batch@v1",
        "outbound.bin.work_plan@v1",
        "outbound.material.decide@v1",
        "outbound.source.empty_decide@v1",
    ],
)
@pytest.mark.parametrize(
    "state,accepted", [("QUEUED", False), ("PREPARING", False), ("EXECUTING", True), ("EXECUTION_COMPLETED", False)]
)
async def test_picking_decisions_require_executing_owner(state, accepted, operation):
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(status=state, workline_id=1))
    )
    assert (
        await PickingTaskConfirmationOwnerService(repository).validate_response_owner(
            object(), picking_task_id=1, operation=operation
        )
        is accepted
    )
