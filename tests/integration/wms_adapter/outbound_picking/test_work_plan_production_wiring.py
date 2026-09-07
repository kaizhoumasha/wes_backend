"""零插件下 work_plan 的响应可靠落库；WAIT 不触发自动重求值。"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from wes_plugin_sdk import wms_operations

from src.app.execution.models import InboundEvidence, InboundEvidenceKind, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.outbound_picking.work_plan_typed import encode_request
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
    picking_confirmation_worker,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY", "cell_ids": ["CELL-2", "CELL-1"]},
        {"result": "NO_WORK"},
        {"result": "WAIT", "retry_after_ms": 1000},
    ],
)
async def test_work_plan_closes_original_obligation_and_preserves_decision(
    confirmation_database, monkeypatch: pytest.MonkeyPatch, data: dict
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, sessions = confirmation_database
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    async with picking_confirmation_worker(
        confirmation_database, server=server, status=PickingTaskStatus.EXECUTING
    ) as (worker, task, _workline, operation_id, now):
        request = encode_request(
            wms_operations.outbound_bin_work_plan(
                operation_id=operation_id, task_id=task.task_id, bin_code="BIN-1", scanned_at=0
            ),
            timestamp=int(timezone.to_utc(now).timestamp() * 1000),
        )
        async with sessions.begin() as db:
            await WmsConfirmationService().create_or_get(
                db,
                operation="outbound.bin.work_plan@v1",
                operation_id=operation_id,
                picking_task_id=task.id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
        worker.start()
        dispatch = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
        assert worker.result(worker.send(dispatch)) == 1
        async with sessions() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == data["result"]
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["data"] == data
            assert evidence.material_execution_id is None
            assert evidence.workline_id is None
            assert (await db.get(PickingTask, task.id)).status == PickingTaskStatus.EXECUTING
            assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
        assert worker.result(worker.send(dispatch)) == 0
        assert server.requests == [{"path": "/api/v1/wes/decisions", "envelope": request}]
