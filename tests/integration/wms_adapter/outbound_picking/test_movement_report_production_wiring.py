"""零业务插件下，既有 movement_report 义务仍通过真实 worker/HTTP 可靠派发。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from wes_plugin_sdk import PickingBinCell, PickingNgZone, PickingRackSlot, wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    MaterialExecution,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.outbound_picking.movement_report_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.movement_report_wire import MATERIAL_MOVEMENT_REPORT_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.app.wms_integration.outbound_picking.services import MaterialMovementReportResultReader
from src.utils.canonical_json import canonical_json_digest
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
@pytest.mark.parametrize("material_owner", [False, True])
async def test_existing_movement_report_dispatches_without_plugins_and_replays_without_duplicate_http(
    confirmation_database,
    monkeypatch: pytest.MonkeyPatch,
    ng: bool,
    code: str,
    material_owner: bool,
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    _, integration_session_factory = confirmation_database
    server = ConfirmationServer(status_code=200, code=code, data={})
    status = PickingTaskStatus.EXECUTION_COMPLETED if ng else PickingTaskStatus.EXECUTING
    async with picking_confirmation_worker(confirmation_database, server=server, status=status) as (
        worker,
        task,
        workline,
        operation_id,
        now,
    ):
        async with integration_session_factory.begin() as db:
            material_id = None
            if material_owner:
                evidence = InboundEvidence(
                    kind="DEVICE_EVENT",
                    device_code="FIXTURE-REPORT-READER",
                    source_identity="fixture-material:" + operation_id,
                    normalized_payload={},
                    payload_digest=canonical_json_digest({}),
                    workline_id=workline.id,
                    received_at=now,
                    processed_at=now,
                    apply_status="APPLIED",
                )
                db.add(evidence)
                await db.flush()
                material = MaterialExecution(
                    execution_code=operation_id,
                    material_trace_id=operation_id,
                    workline_id=workline.id,
                    admission_received_at=now,
                    admission_evidence_id=evidence.id,
                    status="RUNNING",
                    last_transition_reason="FIXTURE_ADMISSION",
                    last_transition_evidence_id=evidence.id,
                    status_changed_at=now,
                )
                db.add(material)
                await db.flush()
                material_id = material.id
            request = encode_request(
                wms_operations.outbound_material_movement_report(
                    operation_id=operation_id,
                    task_id=task.task_id,
                    source_locator=PickingBinCell("RACK-1", "A", "BIN-1", "CELL-1"),
                    barcode=" 原始扫码 ",
                    to_locator=PickingNgZone("NG-1") if ng else PickingRackSlot("TARGET-1", "B", "SLOT-1"),
                    occurred_at=123,
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            await WmsConfirmationService().create_or_get(
                db,
                operation=MATERIAL_MOVEMENT_REPORT_OPERATION,
                operation_id=operation_id,
                picking_task_id=None if material_owner else task.id,
                material_execution_id=material_id,
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
            if material_owner:
                snapshot = await MaterialMovementReportResultReader().by_id(
                    db,
                    material_execution_id=material_id,
                    confirmation_id=confirmation.id,
                )
                assert snapshot.intent.barcode == " 原始扫码 " and snapshot.intent.occurred_at == 123
                assert snapshot.evidence_id == evidence.id and snapshot.outcome.result.duplicate is ng
                assert (await db.get(MaterialExecution, material_id)).status == "RUNNING"
            persisted = await db.get(PickingTask, task.id)
            assert persisted.status == status
        assert len(server.requests) == 1
        assert all(item == {"path": "/api/v1/wes/facts", "envelope": request} for item in server.requests)
        assert worker.result(worker.send(_DISPATCH)) == 0
        assert len(server.requests) == 1
        if material_owner:
            async with integration_session_factory.begin() as db:
                await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id == material_id))
                await db.execute(delete(InboundEvidence).where(InboundEvidence.material_execution_id == material_id))
                await db.execute(delete(MaterialExecution).where(MaterialExecution.id == material_id))
                await db.execute(
                    delete(InboundEvidence).where(InboundEvidence.source_identity == "fixture-material:" + operation_id)
                )
