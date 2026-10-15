"""扫码到位闭合原投箱任务，来源可追溯且不冒充 WMS 结果。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.execution.models import InboundEvidence
from src.app.transport.contracts import HandoffPosition, TransportContractError
from src.app.transport.models import TransportCallbackReceipt, TransportEvidence, TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from tests.support.transport_callbacks import record_valid_callback

NOW = datetime(2026, 10, 16)
SCAN_ID = 990000000001


async def _case(db_engine, count=1):
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    projections = SimpleNamespace(
        lock_workline_authority=AsyncMock(), apply_transport_result=AsyncMock(return_value=None)
    )
    service = TransportService(
        sessions,
        TransportRepository(),
        object(),
        result_timeout=timedelta(seconds=420),
        position_projections=projections,
    )
    async with sessions.begin() as db:
        for model in (TransportEvidence, TransportCallbackReceipt, TransportMember, TransportTask):
            await db.execute(delete(model))
        await db.execute(
            delete(InboundEvidence).where(
                InboundEvidence.source_identity.in_(("transport-scan-arrival:0", "transport-scan-arrival:1"))
            )
        )
        task = TransportTask(
            transport_task_id="original",
            client_request_id="original-client",
            kind="BIN_MOVE",
            request_digest="a" * 64,
            caller_json={"workline_id": "7"},
            authority_workline_id=7,
            request_json={},
            submit_operation_id="original-operation",
            submit_timestamp_ms=1,
            submit_request_body="frozen-body",
            submit_request_body_digest="b" * 64,
            status="ACCEPTED",
            created_at=NOW,
            updated_at=NOW,
        )
        db.add(task)
        for index in range(count):
            db.add(
                TransportMember(
                    transport_task_id="original",
                    ordinal=index,
                    object_type="BIN",
                    object_id=f"A{index:09}",
                    source_json={"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": str(index)},
                    target_json={"kind": "HANDOFF_POSITION", "location_code": "INLET"},
                    updated_at=NOW,
                )
            )
            db.add(
                InboundEvidence(
                    id=SCAN_ID + index,
                    kind="DEVICE_EVENT",
                    source_identity=f"transport-scan-arrival:{index}",
                    payload_digest="c" * 64,
                    normalized_payload={
                        "event_type": "SCAN_COMPLETED",
                        "is_debug": False,
                        "data": {"bin_code": f"A{index:09}-B"},
                    },
                    received_at=NOW,
                    apply_status="APPLIED",
                    workline_id=7,
                    device_code="SCAN1",
                    contract_key="third_party_integration",
                    contract_version="1.1",
                )
            )
    return service, sessions, projections


@pytest.mark.asyncio
async def test_all_scanned_members_close_original_task_once_without_wms_evidence(db_engine):
    service, sessions, projections = await _case(db_engine, count=2)
    for index in range(2):
        async with sessions.begin() as db:
            await service.confirm_bin_arrival_in_session(
                db,
                transport_task_id="original",
                bin_code=f"A{index:09}",
                target=HandoffPosition("INLET"),
                source_evidence_id=SCAN_ID + index,
            )
            task = await service._repository.get_task(db, "original")
            assert task.status == ("ACCEPTED" if index == 0 else "SUCCEEDED")
            assert task.outcome_version == index
            assert task.last_applied_wms_outcome_revision == task.published_outcome_version == 0
            assert task.transport_task_id == "original" and task.client_request_id == "original-client"
            assert task.submit_operation_id == "original-operation" and task.submit_request_body == "frozen-body"
            assert await db.scalar(select(TransportEvidence.id)) is None
            assert await db.scalar(select(TransportCallbackReceipt.id)) is None
    async with sessions.begin() as db:
        await service.confirm_bin_arrival_in_session(
            db,
            transport_task_id="original",
            bin_code="A000000001",
            target=HandoffPosition("INLET"),
            source_evidence_id=SCAN_ID + 1,
        )
        task = await service._repository.get_task(db, "original")
        assert task.outcome_version == 1 and task.reason_code == "SCAN_TARGET_CONFIRMED"
        assert len(task.outcome_json["members"]) == 2
        members = await service._repository.list_members(db, "original")
        assert [m.last_operation_id for m in members] == [f"scan:{SCAN_ID}", f"scan:{SCAN_ID + 1}"]
    assert projections.apply_transport_result.await_count == 2
    publisher = SimpleNamespace(publish=AsyncMock())
    assert await service.publish_pending_outcomes(10, publisher) == 1
    assert await service.publish_pending_outcomes(10, publisher) == 0
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["wrong_line", "unapplied", "debug", "not_scan", "wrong_bin", "wrong_target", "old_scan"]
)
async def test_scan_completion_rejects_unmatched_evidence_without_mutation(db_engine, change):
    service, sessions, _ = await _case(db_engine)
    async with sessions.begin() as db:
        source = await db.get(InboundEvidence, SCAN_ID)
        if change == "wrong_line":
            source.workline_id = 8
        if change == "unapplied":
            source.apply_status = "RECONCILING"
        if change == "debug":
            source.normalized_payload = {**source.normalized_payload, "is_debug": True}
        if change == "not_scan":
            source.normalized_payload = {**source.normalized_payload, "event_type": "OTHER"}
        if change == "old_scan":
            source.received_at = NOW - timedelta(seconds=1)
        with pytest.raises(TransportContractError):
            await service.confirm_bin_arrival_in_session(
                db,
                transport_task_id="original",
                bin_code="OTHER" if change == "wrong_bin" else "A000000000",
                target=HandoffPosition("OTHER" if change == "wrong_target" else "INLET"),
                source_evidence_id=SCAN_ID,
            )
        task = await service._repository.get_task(db, "original")
        assert task.status == "ACCEPTED" and task.outcome_version == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_late_wms_result_is_preserved_after_scan_completion(db_engine, success):
    service, sessions, _ = await _case(db_engine)
    async with sessions.begin() as db:
        await service.confirm_bin_arrival_in_session(
            db,
            transport_task_id="original",
            bin_code="A000000000",
            target=HandoffPosition("INLET"),
            source_evidence_id=SCAN_ID,
        )
    result = {
        "container_id": "A000000000",
        "status": "SUCCEEDED" if success else "FAILED",
        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "INLET"},
    }
    if not success:
        result["failure_code"] = "FAILED_AFTER_SCAN"
    await record_valid_callback(
        service,
        operation_id="019f0000-0000-7000-8000-000000000099",
        transport_task_id="original",
        operation="transport.task.resulted@v1",
        timestamp=1,
        payload={"kind": "BIN_MOVE", "outcome_revision": 1, "results": [result]},
    )
    await service.process_pending_evidence(1)
    async with sessions() as db:
        task = await service._repository.get_task(db, "original")
        evidence = await db.scalar(select(TransportEvidence))
        assert task.status == "SUCCEEDED" and task.outcome_version == 1
        assert task.last_applied_wms_outcome_revision == (1 if success else 0)
        assert evidence.status == ("APPLIED" if success else "CONFLICT")
