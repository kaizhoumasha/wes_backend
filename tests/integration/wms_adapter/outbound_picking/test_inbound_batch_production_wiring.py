"""零业务插件下，既有 inbound_batch 义务仍通过真实 worker/HTTP 可靠派发。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from wes_plugin_sdk import wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack, PickingTaskStatus
from src.app.wms_integration.outbound_picking.services import BinInboundBatchOwnerService
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
    picking_confirmation_worker,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_DISPATCH = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"


@pytest.mark.parametrize(
    ("data", "status_after_obligation", "member_cancelled"),
    [
        ({"result": "RACK_FACE_DONE"}, PickingTaskStatus.EXECUTING, False),
        (
            {
                "result": "READY",
                "bins": [
                    {
                        "bin_code": f"BIN-{index}",
                        "source_locator": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": "SOURCE-RACK-01",
                            "rack_face": "A",
                            "slot_id": f"S-{index}",
                        },
                    }
                    for index in range(5)
                ],
            },
            PickingTaskStatus.EXECUTING,
            False,
        ),
        ({"result": "RACK_FACE_DONE"}, PickingTaskStatus.EXECUTION_COMPLETED, False),
        ({"result": "RACK_FACE_DONE"}, PickingTaskStatus.EXECUTION_COMPLETED, True),
        ({"result": "RACK_FACE_DONE"}, PickingTaskStatus.ARCHIVED, False),
    ],
    ids=["empty-face", "complete-five-bin-face", "completed-parent", "cancelled-member", "archived-parent"],
)
async def test_existing_inbound_batch_dispatches_without_plugins_and_replays_without_duplicate_http(
    confirmation_database,
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, object],
    status_after_obligation: PickingTaskStatus,
    member_cancelled: bool,
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, integration_session_factory = confirmation_database
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    async with picking_confirmation_worker(
        confirmation_database, server=server, status=PickingTaskStatus.EXECUTING
    ) as (worker, task, _workline, operation_id, now):
        async with integration_session_factory.begin() as db:
            request = encode_request(
                wms_operations.outbound_bin_inbound_batch(
                    operation_id=operation_id,
                    task_id=task.task_id,
                    plan_revision=1,
                    rack_id="SOURCE-RACK-01",
                    rack_face="A",
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=task.id,
                    rack_id="SOURCE-RACK-01",
                    rack_face="A",
                    plan_revision=1,
                    source_evidence_id=task.issued_evidence_id,
                )
            )
            await db.flush()
            await WmsConfirmationService(workline_owner=BinInboundBatchOwnerService()).create_or_get(
                db,
                operation=BIN_INBOUND_BATCH_OPERATION,
                operation_id=operation_id,
                workline_id=task.workline_id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
        async with integration_session_factory.begin() as db:
            persisted = await db.get(PickingTask, task.id)
            if member_cancelled:
                cancellation = InboundEvidence(
                    kind=InboundEvidenceKind.WMS_EVENT,
                    source_identity=f"outbound.picking_task.cancel@v1:{operation_id}",
                    operation="outbound.picking_task.cancel@v1",
                    operation_id=operation_id,
                    payload_digest="d" * 64,
                    normalized_payload={"data": {"task_id": task.task_id, "cancel_scope": "PLAN_MEMBERS"}},
                    received_at=now,
                    processed_at=now,
                    apply_status=InboundEvidenceApplyStatus.APPLIED,
                )
                db.add(cancellation)
                await db.flush()
                member = await db.scalar(
                    select(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == task.id)
                )
                member.cancelled_evidence_id = cancellation.id
            persisted.status = status_after_obligation
            if status_after_obligation == PickingTaskStatus.ARCHIVED:
                persisted.archived_at = now
        worker.start()
        worker.result(worker.send(_DISPATCH))
        async with integration_session_factory() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == data["result"]
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["data"] == data
            persisted = await db.get(PickingTask, task.id)
            assert persisted.status == status_after_obligation
        assert len(server.requests) == 1
        assert all(item == {"path": "/api/v1/wes/decisions", "envelope": request} for item in server.requests)
        assert worker.result(worker.send(_DISPATCH)) == 0
        assert len(server.requests) == 1
