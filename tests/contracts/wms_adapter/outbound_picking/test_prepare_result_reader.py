"""准备结果读口只证明原请求已被可靠接纳，不决定插件期限。"""

from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_integration.outbound_picking.services import PickingTaskPrepareResultReader
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest


def accepted():
    request = encode_request(
        wms_operations.outbound_picking_task_prepare(operation_id=new_uuid7(), task_id="TASK", work_line_code="LINE"),
        timestamp=1,
    )
    payload = {"operation_id": request["operation_id"], "code": "PREPARE_ACCEPTED", "timestamp": 2, "data": {}}
    confirmation = N(
        status="COMPLETED",
        operation=request["operation"],
        operation_id=request["operation_id"],
        workline_id=None,
        material_execution_id=None,
        request_payload=request,
        request_digest=canonical_json_digest(request),
        response_evidence_id=9,
        response_result="PREPARE_ACCEPTED",
    )
    evidence = N(
        kind="WMS_RESULT",
        apply_status="APPLIED",
        picking_task_id=7,
        workline_id=None,
        material_execution_id=None,
        operation=request["operation"],
        operation_id=request["operation_id"],
        normalized_payload=payload,
        payload_digest=canonical_json_digest(payload),
        received_at=datetime(2026, 10, 23),
    )
    db = N(scalars=AsyncMock(return_value=N(all=lambda: [confirmation])), get=AsyncMock(return_value=evidence))
    return db, confirmation, evidence


@pytest.mark.asyncio
async def test_prepare_reader_uses_original_local_receipt_and_defers_unaccepted_request():
    db, confirmation, evidence = accepted()
    reader = PickingTaskPrepareResultReader()
    assert (
        await reader.accepted_at_in_session(db, picking_task_id=7, task_id="TASK", workline_code="LINE")
        == evidence.received_at
    )
    confirmation.status = "DISPATCHING"
    assert await reader.accepted_at_in_session(db, picking_task_id=7, task_id="TASK", workline_code="LINE") is None
    db.scalars.return_value = N(all=list)
    assert await reader.accepted_at_in_session(db, picking_task_id=7, task_id="TASK", workline_code="LINE") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    ["request_digest", "request_owner", "result_identity", "result_digest", "result_owner", "unapplied", "duplicate"],
)
async def test_prepare_reader_rejects_identity_or_evidence_drift(bad):
    db, confirmation, evidence = accepted()
    if bad == "request_digest":
        confirmation.request_digest = "wrong"
    elif bad == "request_owner":
        confirmation.request_payload["data"]["task_id"] = "OTHER"
        confirmation.request_digest = canonical_json_digest(confirmation.request_payload)
    elif bad == "result_identity":
        evidence.normalized_payload["operation_id"] = new_uuid7()
        evidence.payload_digest = canonical_json_digest(evidence.normalized_payload)
    elif bad == "result_digest":
        evidence.payload_digest = "wrong"
    elif bad == "result_owner":
        evidence.picking_task_id = 8
    elif bad == "unapplied":
        evidence.apply_status = "PENDING"
    else:
        db.scalars.return_value = N(all=lambda: [confirmation, confirmation])
    with pytest.raises(ValueError):
        await PickingTaskPrepareResultReader().accepted_at_in_session(
            db, picking_task_id=7, task_id="TASK", workline_code="LINE"
        )
