"""任务业务完成后，离场决定仍由零插件宿主可靠派发。"""

import asyncio

import pytest
from sqlalchemy import func, select
from wes_plugin_sdk import TransportRackPosition, wms_operations

from src.app.execution.models import InboundEvidence, InboundEvidenceKind, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import WmsConfirmationService
from src.app.transport.models import TransportTask
from src.app.wms_adapter.outbound_picking.departure_typed import encode_request
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.app.wms_integration.outbound_picking.services.rack_departure import (
    RackDepartureResultReader,
    RackDepartureScheduler,
)
from src.core.task_queue_gateway import DISPATCH_WMS_CONFIRMATIONS_TASK, task_queue_gateway
from src.core.transaction_wakeup import _pending
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
    picking_confirmation_worker,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize(
    "state,data",
    [
        (
            PickingTaskStatus.EXECUTING,
            {"result": "READY", "rack_destination": {"type": "ZONE", "location_code": "STORE-1"}},
        ),
        (
            PickingTaskStatus.EXECUTION_COMPLETED,
            {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "STORE-1"}},
        ),
        (PickingTaskStatus.EXECUTION_COMPLETED, {"result": "WAIT", "retry_after_ms": 1000}),
    ],
)
async def test_departure_persists_decision_without_reopening_task_or_starting_transport(
    confirmation_database, monkeypatch: pytest.MonkeyPatch, state: PickingTaskStatus, data: dict
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, sessions = confirmation_database
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    async with picking_confirmation_worker(confirmation_database, server=server, status=state) as (
        worker,
        task,
        _workline,
        operation_id,
        now,
    ):
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=operation_id,
            task_id=task.task_id,
            rack_id="RACK-1",
            current_location=TransportRackPosition("WORK-1"),
            current_face="面 A",
        )
        request = encode_request(
            intent,
            timestamp=int(timezone.to_utc(now).timestamp() * 1000),
        )
        worker.start()
        dispatched = []
        monkeypatch.setattr(
            task_queue_gateway,
            "enqueue_wms_confirmations",
            lambda: dispatched.append(worker.send(DISPATCH_WMS_CONFIRMATIONS_TASK)),
        )
        async with sessions.begin() as db:
            scheduler = RackDepartureScheduler(WmsConfirmationService())
            for _ in range(2):
                await scheduler.create_in_session(db, intent, picking_task_id=task.id, created_at=now)
            assert dispatched == []
        if _pending:
            await asyncio.gather(*tuple(_pending))
        assert len(dispatched) == 1
        assert worker.result(dispatched[0]) == 1
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
            snapshot = await RackDepartureResultReader().latest(db, task.id, "RACK-1")
            assert snapshot is not None and snapshot.intent.operation_id == operation_id
            assert snapshot.outcome is not None and snapshot.evidence_id == evidence.id
            assert await RackDepartureResultReader().latest(db, task.id, "OTHER-RACK") is None
            assert (await db.get(PickingTask, task.id)).status == state
            assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
            assert await db.scalar(select(func.count()).select_from(TransportTask)) == 0
        assert worker.result(worker.send(DISPATCH_WMS_CONFIRMATIONS_TASK)) == 0
        assert "activate_picking_task_plans_batch" in worker.confirmation_log_path.read_text()
        assert server.requests == [{"path": "/api/v1/wes/decisions", "envelope": request}]
