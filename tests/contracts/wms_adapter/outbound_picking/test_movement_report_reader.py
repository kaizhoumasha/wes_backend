"""当前报告的 typed 读取；业务关闭规则由插件测试证明。"""

from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import PickingBinCell, PickingRackSlot, wms_operations

from src.app.wms_adapter.outbound_picking.movement_report_typed import encode_request
from src.utils.canonical_json import canonical_json_digest


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["RECORDED", "DUPLICATE"])
@pytest.mark.parametrize(
    "drift",
    [
        None,
        "owner",
        "request_digest",
        "operation_id",
        "response_owner",
        "response_identity",
        "response_digest",
        "result",
        "completed_at",
    ],
)
async def test_reader_requires_exact_current_report_and_typed_result(monkeypatch, code, drift):
    from src.app.wms_integration.outbound_picking.services import material_movement_report as module

    intent = wms_operations.outbound_material_movement_report(
        operation_id="019f3405-2200-7b01-8b01-000000000001",
        task_id="TASK-1",
        source_locator=PickingBinCell("RACK", "A", "BIN", "CELL"),
        barcode=" 原文-barcode ",
        to_locator=PickingRackSlot("TARGET", "A", "S1"),
        occurred_at=123,
    )
    request = encode_request(intent, timestamp=1)
    response = {"operation_id": intent.operation_id, "timestamp": 2, "code": code, "data": {}}
    now = datetime(2026, 10, 17)
    row = N(
        id=77,
        operation="outbound.material.movement_report@v1",
        operation_id=intent.operation_id,
        material_execution_id=42,
        picking_task_id=None,
        workline_id=None,
        status="COMPLETED",
        request_payload=request,
        request_digest=canonical_json_digest(request),
        response_evidence_id=91,
        response_result=code,
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
        published_at=None,
    )
    if drift == "owner":
        row.material_execution_id = 43
    elif drift == "request_digest":
        row.request_digest = "wrong"
    elif drift == "operation_id":
        row.operation_id = "wrong"
    elif drift == "response_owner":
        evidence.material_execution_id = 43
    elif drift == "response_identity":
        evidence.operation_id = "wrong"
    elif drift == "response_digest":
        evidence.payload_digest = "wrong"
    elif drift == "result":
        row.response_result = "DUPLICATE" if code == "RECORDED" else "RECORDED"
    elif drift == "completed_at":
        row.completed_at = None
    monkeypatch.setattr(module.material_execution_repository, "get_by_id", AsyncMock(return_value=N(workline_id=7)))
    monkeypatch.setattr(module.inbound_evidence_repository, "get_by_id", AsyncMock(return_value=evidence))
    reader = module.MaterialMovementReportResultReader(N(get_by_id=AsyncMock(return_value=row)))
    if drift:
        with pytest.raises(ValueError):
            await reader.by_id(None, material_execution_id=42, confirmation_id=77)
    else:
        snapshot = await reader.by_id(None, material_execution_id=42, confirmation_id=77)
        assert snapshot.intent == intent and snapshot.outcome.result.duplicate is (code == "DUPLICATE")
        assert snapshot.evidence_id == 91 and snapshot.published_at is None
