"""零业务插件下，既有 inbound_batch 义务仍通过真实 worker/HTTP 可靠派发。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from wes_plugin_sdk import wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
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


@pytest.mark.parametrize("result", ["NO_BATCH", "RACK_FACE_DONE"])
async def test_existing_inbound_batch_dispatches_without_plugins_and_replays_without_duplicate_http(
    confirmation_database,
    monkeypatch: pytest.MonkeyPatch,
    result: str,
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, integration_session_factory = confirmation_database
    server = ConfirmationServer(
        status_code=200,
        code="DECIDED",
        data={"result": result, **({"retry_after_ms": 1000} if result == "NO_BATCH" else {})},
    )
    async with picking_confirmation_worker(
        confirmation_database, server=server, status=PickingTaskStatus.EXECUTING
    ) as (worker, task, _workline, operation_id, now):
        async with integration_session_factory.begin() as db:
            request = encode_request(
                wms_operations.outbound_bin_inbound_batch(
                    operation_id=operation_id,
                    task_id=task.task_id,
                    rack_id="SOURCE-RACK-01",
                    rack_face="A",
                    max_bin_count=2,
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            await WmsConfirmationService().create_or_get(
                db,
                operation=BIN_INBOUND_BATCH_OPERATION,
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
            assert confirmation.response_result == result
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["data"]["result"] == result
            persisted = await db.get(PickingTask, task.id)
            assert persisted.status == PickingTaskStatus.EXECUTING
        assert len(server.requests) == 1
        assert all(item == {"path": "/api/v1/wes/decisions", "envelope": request} for item in server.requests)
        assert worker.result(worker.send(_DISPATCH)) == 0
        assert len(server.requests) == 1
