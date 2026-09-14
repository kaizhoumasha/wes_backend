"""插件只提交 typed intent，WMS 可靠义务由宿主创建。"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import wes_plugin_sdk as sdk

from src.app.wms_integration.outbound_picking.services.manual_bin_admission import (
    ManualBinAdmissionOwnerService,
    ManualBinAdmissionScheduler,
)


class _Confirmations:
    def __init__(self) -> None:
        self.kwargs = None

    async def create_or_get(self, db, **kwargs):  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        return SimpleNamespace(confirmation=SimpleNamespace(next_attempt_at=None), duplicate=False)


@pytest.mark.asyncio
async def test_typed_admission_creates_workline_owned_obligation_and_due_retry() -> None:
    confirmations = _Confirmations()
    scheduler = ManualBinAdmissionScheduler(confirmations)
    now = datetime(2026, 9, 13, 12)
    intent = sdk.wms_operations.outbound_manual_bin_work_admission(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        task_id="PICK-001",
        bin_code="A000000001",
        scanned_at=1_788_389_899_900,
    )

    await scheduler.create_in_session(
        object(), intent, workline_id=7, created_at=now, not_before=now + timedelta(seconds=1)
    )

    assert confirmations.kwargs["workline_id"] == 7
    assert confirmations.kwargs["operation"] == "outbound.manual_bin.work_admission_decide@v1"
    assert confirmations.kwargs["request_payload"]["data"] == {
        "task_id": "PICK-001",
        "bin_code": "A000000001",
        "scanned_at": 1_788_389_899_900,
    }


@pytest.mark.asyncio
async def test_manual_bin_admission_owner_requires_same_executing_workline() -> None:
    class Tasks:
        async def get_by_task_id_for_update(self, db, task_id):  # type: ignore[no-untyped-def]
            assert task_id == "PICK-001"
            return SimpleNamespace(workline_id=7, status="EXECUTING", task_type="MANUAL")

    owner = ManualBinAdmissionOwnerService(Tasks())
    payload = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": "outbound.manual_bin.work_admission_decide@v1",
        "timestamp": 1_788_389_900_000,
        "data": {"task_id": "PICK-001", "bin_code": "A000000001", "scanned_at": 1_788_389_899_900},
    }

    assert await owner.validate_owner(object(), workline_id=7, request_payload=payload)
    assert not await owner.validate_owner(object(), workline_id=8, request_payload=payload)
