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
from src.utils.timezone import timezone
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
                        "timestamp": int(timezone.to_utc(NOW).timestamp() * 1000),
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


@pytest.mark.asyncio
@pytest.mark.parametrize("wms_result", ["matching", "failed", "wrong_target"])
async def test_partial_scan_then_wms_result_preserves_scan_fact_identity(db_engine, wms_result):
    service, sessions, projections = await _case(db_engine, count=2)
    async with sessions.begin() as db:
        await service.confirm_bin_arrival_in_session(
            db,
            transport_task_id="original",
            bin_code="A000000000",
            target=HandoffPosition("INLET"),
            source_evidence_id=SCAN_ID,
        )
        member = (await service._repository.list_members(db, "original"))[0]
        scan_fact = member.model_dump()
    operation_id = "019f0000-0000-7000-8000-000000000098"
    results = [
        {
            "container_id": f"A{index:09}",
            "status": "SUCCEEDED",
            "final_position": {"kind": "HANDOFF_POSITION", "location_code": "INLET"},
        }
        for index in range(2)
    ]
    if wms_result == "failed":
        results[0].update(status="FAILED", failure_code="CONTRADICTS_SCAN")
    if wms_result == "wrong_target":
        results[0]["final_position"] = {"kind": "HANDOFF_POSITION", "location_code": "ELSEWHERE"}
    await record_valid_callback(
        service,
        operation_id=operation_id,
        transport_task_id="original",
        operation="transport.task.resulted@v1",
        timestamp=1,
        payload={"kind": "BIN_MOVE", "outcome_revision": 1, "results": results},
    )
    await service.process_pending_evidence(1)
    matching = wms_result == "matching"
    async with sessions() as db:
        task = await service._repository.get_task(db, "original")
        members = await service._repository.list_members(db, "original")
        assert task.status == ("SUCCEEDED" if matching else "RECONCILING")
        assert task.outcome_version == 1
        assert task.last_applied_wms_outcome_revision == int(matching)
        assert members[0].model_dump() == scan_fact
        assert members[1].last_operation_id == (operation_id if matching else None)
        assert projections.apply_transport_result.await_count == (2 if matching else 1)
        assert (await db.scalar(select(TransportEvidence))).status == ("APPLIED" if matching else "CONFLICT")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_timestamp", ["old", None, True, "1792108800000"])
async def test_delayed_or_invalid_scan_event_time_does_not_close_new_transport(db_engine, event_timestamp):
    service, sessions, projections = await _case(db_engine)
    async with sessions.begin() as db:
        source = await db.get(InboundEvidence, SCAN_ID)
        source.received_at = NOW + timedelta(seconds=10)
        source.normalized_payload = {
            **source.normalized_payload,
            "timestamp": int(timezone.to_utc(NOW).timestamp() * 1000) - 1
            if event_timestamp == "old"
            else event_timestamp,
        }
        with pytest.raises(TransportContractError):
            await service.confirm_bin_arrival_in_session(
                db,
                transport_task_id="original",
                bin_code="A000000000",
                target=HandoffPosition("INLET"),
                source_evidence_id=SCAN_ID,
            )
        task = await service._repository.get_task(db, "original")
        member = (await service._repository.list_members(db, "original"))[0]
        assert task.status == "ACCEPTED" and task.outcome_version == 0
        assert member.status == "PENDING" and member.last_operation_id is None
        projections.apply_transport_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_refines_failed_member_with_unknown_position_on_original_task(db_engine):
    service, sessions, _ = await _case(db_engine, count=2)
    await record_valid_callback(
        service,
        operation_id="019f0000-0000-7000-8000-000000000097",
        transport_task_id="original",
        operation="transport.task.resulted@v1",
        timestamp=1,
        payload={
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {
                    "container_id": "A000000000",
                    "status": "FAILED",
                    "position_unknown": True,
                    "failure_code": "POSITION_UNKNOWN",
                },
                {
                    "container_id": "A000000001",
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "HANDOFF_POSITION", "location_code": "INLET"},
                },
            ],
        },
    )
    await service.process_pending_evidence(1)
    async with sessions.begin() as db:
        task = await service._repository.get_task(db, "original")
        member = (await service._repository.list_members(db, "original"))[0]
        assert task.status == "RECONCILING" and member.status == "FAILED" and member.position_unknown
        await service.confirm_bin_arrival_in_session(
            db,
            transport_task_id="original",
            bin_code="A000000000",
            target=HandoffPosition("INLET"),
            source_evidence_id=SCAN_ID,
        )
        assert member.status == "SUCCEEDED" and not member.position_unknown
        assert member.last_operation_id == f"scan:{SCAN_ID}" and member.failure_code is None
        assert task.status == "SUCCEEDED" and task.reason_code == "SCAN_TARGET_CONFIRMED"
        assert task.outcome_version == 2 and task.last_applied_wms_outcome_revision == 1
        assert task.submit_operation_id == "original-operation" and task.submit_request_body == "frozen-body"


@pytest.mark.asyncio
@pytest.mark.parametrize("member_status", ["SUCCEEDED", "FAILED"])
async def test_scan_does_not_rewrite_known_terminal_member_of_reconciling_task(db_engine, member_status):
    service, sessions, projections = await _case(db_engine, count=2)
    async with sessions.begin() as db:
        task = await service._repository.get_task(db, "original")
        task.status = "RECONCILING"
        member = (await service._repository.list_members(db, "original"))[0]
        member.status = member_status
        member.final_position_json = member.target_json if member_status == "SUCCEEDED" else member.source_json
        member.position_unknown = False
        member.last_operation_id = "original-terminal-fact"
        before = member.model_dump()
        await service.confirm_bin_arrival_in_session(
            db,
            transport_task_id="original",
            bin_code="A000000000",
            target=HandoffPosition("INLET"),
            source_evidence_id=SCAN_ID,
        )
        assert member.model_dump() == before
        assert task.status == "RECONCILING" and task.outcome_version == 0
        projections.apply_transport_result.assert_not_awaited()
