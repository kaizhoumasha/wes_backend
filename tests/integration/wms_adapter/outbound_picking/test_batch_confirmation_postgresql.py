"""批次可重复求值，但一次性 prepare 仍有任务唯一围栏。"""

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.execution.services import WmsConfirmationLifecycleService
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    confirmation_database,
    seed_picking_owner,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize("operation", ["outbound.bin.inbound_batch@v1", "outbound.picking_task.prepare@v1"])
async def test_task_allows_new_batch_identity_but_only_one_prepare(confirmation_database, operation):
    _, sessions = confirmation_database
    now = timezone.now_for_db()
    async with sessions.begin() as db:
        task, _ = await seed_picking_owner(
            db,
            operation_id=new_uuid7(),
            issued_operation_id=new_uuid7(),
            task_key=new_uuid7(),
            now=now,
            status=PickingTaskStatus.EXECUTING,
        )
        task_id = task.id
    service = WmsConfirmationLifecycleService()

    async def create():
        operation_id = new_uuid7()
        async with sessions.begin() as db:
            return await service.create_or_get(
                db,
                operation=operation,
                operation_id=operation_id,
                picking_task_id=task_id,
                request_payload={"operation": operation, "operation_id": operation_id},
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )

    first = await create()
    if operation == "outbound.picking_task.prepare@v1":
        with pytest.raises(IntegrityError) as error:
            await create()
        assert "ux_wms_confirmations_picking_task_prepare" in str(error.value)
    else:
        second = await create()
        assert first.confirmation.id != second.confirmation.id
        assert not second.duplicate
