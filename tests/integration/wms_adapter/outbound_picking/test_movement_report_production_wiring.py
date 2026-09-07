"""零业务插件下，既有 movement_report 义务仍通过真实 worker/HTTP 可靠派发。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from wes_plugin_sdk import PickingBinCell, PickingNgZone, PickingRackSlot, wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.outbound_picking.movement_report_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.movement_report_wire import MATERIAL_MOVEMENT_REPORT_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
    picking_confirmation_worker,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_DISPATCH = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"


@pytest.mark.parametrize("ng,code", [(False, "RECORDED"), (True, "DUPLICATE")])
async def test_existing_movement_report_dispatches_without_plugins_and_replays_without_duplicate_http(
    confirmation_database,
    monkeypatch: pytest.MonkeyPatch,
    ng: bool,
    code: str,
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, integration_session_factory = confirmation_database
    server = ConfirmationServer(status_code=200, code=code, data={})
    status = PickingTaskStatus.EXECUTION_COMPLETED if ng else PickingTaskStatus.EXECUTING
    async with picking_confirmation_worker(confirmation_database, server=server, status=status) as (
        worker,
        task,
        _workline,
        operation_id,
        now,
    ):
        async with integration_session_factory.begin() as db:
            request = encode_request(
                wms_operations.outbound_material_movement_report(
                    operation_id=operation_id,
                    task_id=task.task_id,
                    source_locator=PickingBinCell("RACK-1", "A", "BIN-1", "CELL-1"),
                    pkg_id=" 原始扫码 ",
                    to_locator=PickingNgZone("NG-1") if ng else PickingRackSlot("TARGET-1", "B", "SLOT-1"),
                    occurred_at=123,
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            await WmsConfirmationService().create_or_get(
                db,
                operation=MATERIAL_MOVEMENT_REPORT_OPERATION,
                operation_id=operation_id,
                picking_task_id=task.id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
        worker.start()
        worker.result(worker.send(_DISPATCH))
        async with integration_session_factory() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == code
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["code"] == code
            assert decode_outcome(evidence.normalized_payload).result.duplicate is ng
            persisted = await db.get(PickingTask, task.id)
            assert persisted.status == status
        assert len(server.requests) == 1
        assert all(item == {"path": "/api/v1/wes/facts", "envelope": request} for item in server.requests)
        assert worker.result(worker.send(_DISPATCH)) == 0
        assert len(server.requests) == 1
