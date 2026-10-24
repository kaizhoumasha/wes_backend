"""Work 请求从 typed intent 冻结为任务所属的唯一可靠义务。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from wes_plugin_sdk import wms_operations

from src.app.execution.models import WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService

NOW = datetime(2026, 10, 16)
IDENTITY = "019f3405-2200-7b01-8b01-000000000001"


class Confirmations:
    row = None

    async def lock_identity(self, *args):
        pass

    async def get_by_identity_for_update(self, *args):
        return self.row

    async def add(self, db, confirmation):
        self.row = confirmation
        return confirmation

    async def flush(self, db):
        pass


class Tasks:
    async def get_by_id_for_update(self, db, task_id):
        return SimpleNamespace(id=task_id, task_id="TASK-1", workline_id=7)


@pytest.mark.asyncio
async def test_work_plan_replays_frozen_request_and_rejects_same_identity_drift():
    from src.app.wms_integration.outbound_picking.services.bin_work_plan import BinWorkPlanScheduler

    rows = Confirmations()
    service = BinWorkPlanScheduler(WmsConfirmationLifecycleService(rows), tasks=Tasks())
    intent = wms_operations.outbound_bin_work_plan(
        operation_id=IDENTITY, task_id="TASK-1", bin_code="BIN-1", scanned_at=123
    )
    await service.create_in_session(object(), intent, picking_task_id=12, created_at=NOW)
    first = rows.row
    await service.create_in_session(object(), intent, picking_task_id=12, created_at=NOW)
    assert rows.row is first
    assert first.picking_task_id == 12
    assert first.material_execution_id is None and first.workline_id is None
    assert first.request_payload == {
        "operation": "outbound.bin.work_plan@v1",
        "operation_id": IDENTITY,
        "timestamp": 1792108800000,
        "data": {"task_id": "TASK-1", "bin_code": "BIN-1", "scanned_at": 123},
    }
    changed = wms_operations.outbound_bin_work_plan(
        operation_id=IDENTITY, task_id="TASK-1", bin_code="BIN-2", scanned_at=123
    )
    with pytest.raises(ValueError):
        await service.create_in_session(object(), changed, picking_task_id=12, created_at=NOW)
    assert first.request_payload["data"]["bin_code"] == "BIN-1"
    assert first.status == WmsConfirmationStatus.RECONCILING


@pytest.mark.asyncio
async def test_work_plan_cannot_bind_a_different_task_to_the_request():
    from src.app.wms_integration.outbound_picking.services.bin_work_plan import BinWorkPlanScheduler

    rows = Confirmations()
    service = BinWorkPlanScheduler(WmsConfirmationLifecycleService(rows), tasks=Tasks())
    intent = wms_operations.outbound_bin_work_plan(
        operation_id=IDENTITY, task_id="TASK-OTHER", bin_code="BIN-1", scanned_at=123
    )
    with pytest.raises(ValueError):
        await service.create_in_session(object(), intent, picking_task_id=12, created_at=NOW)
    assert rows.row is None
