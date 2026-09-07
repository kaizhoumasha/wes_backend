"""零业务插件下，既有 prepare 义务仍通过真实 worker/HTTP 可靠派发。"""

from __future__ import annotations

import asyncio
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
from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
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


@pytest.mark.parametrize("unavailable_first", [False, True])
async def test_existing_prepare_dispatches_without_plugins_and_replays_without_duplicate_http(
    confirmation_database,
    monkeypatch: pytest.MonkeyPatch,
    unavailable_first: bool,
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, integration_session_factory = confirmation_database
    server = ConfirmationServer(status_code=202, code="PREPARE_ACCEPTED", data={}, unavailable_first=unavailable_first)
    async with picking_confirmation_worker(
        confirmation_database, server=server, status=PickingTaskStatus.PREPARING
    ) as (worker, task, workline, operation_id, now):
        async with integration_session_factory.begin() as db:
            request = encode_request(
                wms_operations.outbound_picking_task_prepare(
                    operation_id=operation_id, task_id=task.task_id, work_line_code=workline.line_code
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            await WmsConfirmationService().create_or_get(
                db,
                operation=PICKING_TASK_PREPARE_OPERATION,
                operation_id=operation_id,
                picking_task_id=task.id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
        worker.start()
        worker.result(worker.send(_DISPATCH))
        if unavailable_first:
            async with integration_session_factory() as db:
                pending = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
                assert pending.status == WmsConfirmationStatus.PENDING
                assert pending.operation_id == operation_id
                assert pending.request_payload == request
                assert pending.next_attempt_at is not None
                wait_seconds = max(0, (pending.next_attempt_at - timezone.now_for_db()).total_seconds())
            await asyncio.sleep(wait_seconds + 0.1)
            worker.result(worker.send(_DISPATCH))
        async with integration_session_factory() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == "PREPARE_ACCEPTED"
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["code"] == "PREPARE_ACCEPTED"
            persisted = await db.get(PickingTask, task.id)
            assert persisted.status == PickingTaskStatus.PREPARING
        assert len(server.requests) == (2 if unavailable_first else 1)
        assert all(item == {"path": "/api/v1/wes/decisions", "envelope": request} for item in server.requests)
        assert worker.result(worker.send(_DISPATCH)) == 0
        assert len(server.requests) == (2 if unavailable_first else 1)
