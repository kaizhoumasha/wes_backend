"""departure typed 读取端口：应用前读原响应，派生动作仍要求已发布。"""

import pytest
from sqlalchemy import select
from wes_plugin_sdk import RackDepartureReady, RackDepartureWait, TransportRackPosition, wms_operations

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.wms_integration.outbound_picking.services.rack_departure import (
    RackDepartureResultReader,
    RackDepartureScheduler,
)
from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    confirmation_database,
    seed_picking_owner,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize("result", ["WAIT", "READY"])
async def test_exact_typed_read_separates_application_from_publication(confirmation_database, result):
    _, sessions = confirmation_database
    now = timezone.now_for_db()
    operation_id = new_uuid7()
    async with sessions.begin() as db:
        _, line = await seed_picking_owner(
            db,
            operation_id=new_uuid7(),
            issued_operation_id=new_uuid7(),
            task_key=new_uuid7(),
            now=now,
            status="EXECUTING",
        )
        line.is_active = True
        await db.flush()
        line_id = line.id
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=operation_id,
            task_id=None,
            rack_id="RACK-1",
            current_location=TransportRackPosition("WORK-1"),
            current_face="A",
        )
        await RackDepartureScheduler(
            WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService())
        ).create_in_session(db, intent, workline_id=line_id, created_at=now)
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
        data = {"result": result}
        if result == "WAIT":
            data["retry_after_ms"] = 1000
        else:
            data["rack_destination"] = {"type": "ZONE", "location_code": "STORE-1"}
        payload = {"operation_id": operation_id, "timestamp": 1, "code": "DECIDED", "data": data}
        evidence = InboundEvidence(
            kind="WMS_RESULT",
            operation=request.operation,
            operation_id=operation_id,
            source_identity=request.operation + ":" + operation_id,
            workline_id=line_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=now,
        )
        db.add(evidence)
        await db.flush()
        evidence_id = evidence.id
        request.status, request.response_evidence_id, request.response_result, request.completed_at = (
            "COMPLETED",
            evidence_id,
            result,
            now,
        )
    reader = RackDepartureResultReader()
    async with sessions.begin() as db:
        snapshot = await reader.for_evidence(
            db, workline_id=line_id, operation_id=operation_id, rack_id="RACK-1", evidence_id=evidence_id
        )
        assert type(snapshot.outcome.result) is (RackDepartureWait if result == "WAIT" else RackDepartureReady)
        with pytest.raises(ValueError, match="application evidence"):
            await reader.for_evidence(
                db, workline_id=line_id, operation_id=operation_id, rack_id="RACK-1", evidence_id=evidence_id + 1
            )
        with pytest.raises(ValueError, match="not published"):
            await reader.by_operation_id(db, workline_id=line_id, operation_id=operation_id, rack_id="RACK-1")
        evidence = await db.get(InboundEvidence, evidence_id)
        evidence.decision_digest = canonical_json_digest({"fixture": "native reader publication"})
        evidence.published_at = now
    async with sessions() as db:
        snapshot = await reader.by_operation_id(db, workline_id=line_id, operation_id=operation_id, rack_id="RACK-1")
        assert snapshot.evidence_id == evidence_id and snapshot.intent.task_id is None
