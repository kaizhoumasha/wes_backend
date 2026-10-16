"""单盘 typed 持久端口只拥有可靠请求，不解释 WAIT 业务时机。"""

from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import PickingBinCell, wms_operations

from src.app.wms_adapter.outbound_picking.material_decide_typed import encode_request
from src.app.wms_integration.outbound_picking.services import material_decide
from src.app.wms_integration.outbound_picking.services.material_decide import MaterialDecideScheduler
from src.utils.canonical_json import canonical_json_digest


@pytest.mark.asyncio
async def test_scheduler_freezes_typed_intent_under_one_material_owner():
    calls = []

    class Confirmations:
        async def create_or_get(self, db, **kwargs):
            calls.append((db, kwargs))
            return N(confirmation=N(id=77))

    intent = wms_operations.outbound_material_decide(
        operation_id="019f3405-2200-7b01-8b01-000000000001",
        task_id="TASK-1",
        source_locator=PickingBinCell("RACK", "A", "BIN", "CELL"),
        barcode=" 原文 ",
        scanned_at=123,
    )
    now, db = datetime(2026, 10, 17), object()
    assert (
        await MaterialDecideScheduler(Confirmations()).create_in_session(
            db,
            intent,
            material_execution_id=42,
            created_at=now,
        )
        == 77
    )
    assert calls[0][0] is db
    data = calls[0][1]
    assert data["operation"] == "outbound.material.decide@v1"
    assert data["operation_id"] == intent.operation_id and data["material_execution_id"] == 42
    assert data["request_payload"]["data"]["barcode"] == " 原文 "
    assert data["request_payload"]["data"]["scanned_at"] == 123
    assert data.get("picking_task_id") is None and data.get("workline_id") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "owner", "request_digest", "response_identity", "response_result"])
async def test_reader_returns_only_exact_owned_typed_request_and_result(monkeypatch, drift):
    intent = wms_operations.outbound_material_decide(
        operation_id="019f3405-2200-7b01-8b01-000000000001",
        task_id="TASK-1",
        source_locator=PickingBinCell("RACK", "A", "BIN", "CELL"),
        barcode="原文",
        scanned_at=123,
    )
    request = encode_request(intent, timestamp=1)
    response = {
        "operation_id": intent.operation_id,
        "timestamp": 2,
        "code": "DECIDED",
        "data": {"result": "WAIT", "retry_after_ms": 1000},
    }
    now = datetime(2026, 10, 17)
    row = N(
        id=77,
        operation="outbound.material.decide@v1",
        operation_id=intent.operation_id,
        material_execution_id=42,
        picking_task_id=None,
        workline_id=None,
        status="COMPLETED",
        request_payload=request,
        request_digest=canonical_json_digest(request),
        response_evidence_id=91,
        response_result="WAIT",
        completed_at=now,
    )
    evidence = N(
        kind="WMS_RESULT",
        apply_status="APPLIED",
        material_execution_id=42,
        workline_id=7,
        picking_task_id=None,
        operation=row.operation,
        operation_id=intent.operation_id,
        normalized_payload=response,
        payload_digest=canonical_json_digest(response),
        published_at=now,
    )
    if drift == "owner":
        row.material_execution_id = 43
    elif drift == "request_digest":
        row.request_digest = "wrong"
    elif drift == "response_identity":
        evidence.operation_id = "wrong"
    elif drift == "response_result":
        row.response_result = "ACCEPT"
    monkeypatch.setattr(
        material_decide.material_execution_repository, "get_by_id", AsyncMock(return_value=N(workline_id=7))
    )
    monkeypatch.setattr(material_decide.inbound_evidence_repository, "get_by_id", AsyncMock(return_value=evidence))
    reader = material_decide.MaterialDecideResultReader(N(get_by_id=AsyncMock(return_value=row)))
    if drift:
        with pytest.raises(ValueError):
            await reader.by_id(None, material_execution_id=42, confirmation_id=77)
    else:
        snapshot = await reader.by_id(None, material_execution_id=42, confirmation_id=77)
        assert snapshot.intent == intent and snapshot.outcome.result.retry_after_ms == 1000
        assert snapshot.evidence_id == 91 and snapshot.completed_at == snapshot.published_at == now
