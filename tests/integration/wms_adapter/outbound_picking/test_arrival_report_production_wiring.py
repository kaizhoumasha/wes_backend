"""零业务插件下，既有 arrival_report 义务仍通过真实 worker/HTTP 可靠派发。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from wes_plugin_sdk import TransportRackPosition, wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.outbound_picking.arrival_report_typed import encode_request
from src.app.wms_adapter.outbound_picking.arrival_report_wire import RETURN_RACK_ARRIVAL_REPORT_OPERATION
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
async def test_existing_arrival_report_dispatches_without_plugins_and_replays_without_duplicate_http(
    confirmation_database,
    monkeypatch: pytest.MonkeyPatch,
    unavailable_first: bool,
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, integration_session_factory = confirmation_database
    server = ConfirmationServer(status_code=200, code="RECORDED", data={}, unavailable_first=unavailable_first)
    async with picking_confirmation_worker(
        confirmation_database, server=server, status=PickingTaskStatus.EXECUTION_COMPLETED
    ) as (worker, task, _workline, operation_id, now):
        async with integration_session_factory.begin() as db:
            request = encode_request(
                wms_operations.outbound_return_rack_arrival_report(
                    operation_id=operation_id,
                    task_id=task.task_id,
                    transport_task_id="TRANSPORT-ARRIVAL-1",
                    outcome_revision=1,
                    rack_id="RETURN-RACK-01",
                    final_position=TransportRackPosition(location_code="RETURN-WORK-01"),
                    arrival_face="A",
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            await WmsConfirmationService().create_or_get(
                db,
                operation=RETURN_RACK_ARRIVAL_REPORT_OPERATION,
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
            assert confirmation.response_result == "RECORDED"
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["code"] == "RECORDED"
            persisted = await db.get(PickingTask, task.id)
            assert persisted.status == PickingTaskStatus.EXECUTION_COMPLETED
        assert len(server.requests) == (2 if unavailable_first else 1)
        assert all(item == {"path": "/api/v1/wes/facts", "envelope": request} for item in server.requests)
        assert worker.result(worker.send(_DISPATCH)) == 0
        assert len(server.requests) == (2 if unavailable_first else 1)
