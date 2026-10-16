"""按当前 Work 的原请求身份读取 typed 结果，业务发布不是读取前置条件。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from wes_plugin_sdk import BinWorkPlanNoWork, BinWorkPlanWait

from src.utils.canonical_json import canonical_json_digest

IDENTITY = "019f3405-2200-7b01-8b01-000000000001"
OPERATION = "outbound.bin.work_plan@v1"


def records(result):
    payload = {
        "operation": OPERATION,
        "operation_id": IDENTITY,
        "timestamp": 0,
        "data": {"task_id": "TASK-1", "bin_code": "BIN-1", "scanned_at": 123},
    }
    row = SimpleNamespace(
        operation=OPERATION,
        operation_id=IDENTITY,
        picking_task_id=12,
        material_execution_id=None,
        workline_id=None,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        status="COMPLETED",
        response_evidence_id=31,
        response_result=result["result"],
        completed_at=datetime(2026, 10, 16),
    )
    evidence = SimpleNamespace(
        id=31,
        kind="WMS_RESULT",
        apply_status="APPLIED",
        operation=OPERATION,
        operation_id=IDENTITY,
        picking_task_id=12,
        material_execution_id=None,
        published_at=None,
        normalized_payload={"operation_id": IDENTITY, "code": "DECIDED", "timestamp": 1, "data": result},
    )
    return row, evidence


class Confirmations:
    def __init__(self, row):
        self.row = row

    async def get_by_identity(self, db, operation, operation_id):
        assert operation == OPERATION and operation_id == IDENTITY
        return self.row


class Evidence:
    def __init__(self, row):
        self.row = row

    async def get_by_id(self, db, evidence_id):
        assert evidence_id == 31
        return self.row


class Tasks:
    async def get_by_id(self, db, task_id):
        return SimpleNamespace(id=task_id, task_id="TASK-1", workline_id=7)


@pytest.mark.parametrize(
    "result,expected",
    [({"result": "NO_WORK"}, BinWorkPlanNoWork()), ({"result": "WAIT", "retry_after_ms": 1000}, BinWorkPlanWait(1000))],
)
@pytest.mark.asyncio
async def test_reader_returns_original_typed_context_before_business_publication(result, expected):
    from src.app.wms_integration.outbound_picking.services.bin_work_plan import BinWorkPlanResultReader

    row, evidence = records(result)
    reader = BinWorkPlanResultReader(confirmations=Confirmations(row), evidences=Evidence(evidence), tasks=Tasks())
    snapshot = await reader.by_operation_id(object(), picking_task_id=12, operation_id=IDENTITY)
    assert snapshot.intent.bin_code == "BIN-1" and snapshot.intent.scanned_at == 123
    assert snapshot.outcome.result == expected
    assert snapshot.evidence_id == 31
    assert evidence.published_at is None


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("confirmation", "picking_task_id", 13),
        ("confirmation", "material_execution_id", 21),
        ("confirmation", "request_digest", "wrong"),
        ("confirmation", "response_result", "READY"),
        ("evidence", "operation_id", "019f3405-2200-7b01-8b01-000000000002"),
        ("evidence", "picking_task_id", 13),
        ("evidence", "apply_status", "PENDING"),
    ],
)
@pytest.mark.asyncio
async def test_reader_rejects_mismatched_frozen_identity(target, field, value):
    from src.app.wms_integration.outbound_picking.services.bin_work_plan import BinWorkPlanResultReader

    row, evidence = records({"result": "NO_WORK"})
    setattr(row if target == "confirmation" else evidence, field, value)
    reader = BinWorkPlanResultReader(confirmations=Confirmations(row), evidences=Evidence(evidence), tasks=Tasks())
    with pytest.raises(ValueError):
        await reader.by_operation_id(object(), picking_task_id=12, operation_id=IDENTITY)
