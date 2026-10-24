"""人工完成是可审计的本地终态，不冒充设备回执或重写当前位置。"""

import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.sys.models.audit_log import AuditLog
from src.app.transport.contracts import HandoffPosition, TransportContractError
from src.app.transport.models import TransportCallbackReceipt, TransportEvidence, TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from tests.support.transport_callbacks import record_valid_callback

BODY = '{"original":true}'
DIGEST = hashlib.sha256(BODY.encode()).hexdigest()
TARGET = {"kind": "HANDOFF_POSITION", "location_code": "CNV0401"}
NOW = datetime(2026, 10, 22)


@pytest.fixture
async def manual_case(db_session):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    projections = SimpleNamespace(lock_workline_authority=AsyncMock(), apply_transport_result=AsyncMock())
    provider = SimpleNamespace(submit=AsyncMock())
    service = TransportService(
        sessions,
        TransportRepository(),
        provider,
        result_timeout=timedelta(seconds=420),
        position_projections=projections,
    )
    async with sessions.begin() as db:
        db.add(
            TransportTask(
                transport_task_id="manual-original",
                client_request_id="manual-client",
                kind="BIN_MOVE",
                request_digest="a" * 64,
                caller_json={"workline_id": "7"},
                authority_workline_id=7,
                request_json={},
                submit_operation_id="manual-submit",
                submit_timestamp_ms=1,
                submit_request_body=BODY,
                submit_request_body_digest=DIGEST,
                status="ACCEPTED",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        db.add(
            TransportMember(
                transport_task_id="manual-original",
                ordinal=0,
                object_type="BIN",
                object_id="A000000625",
                source_json={"kind": "RACK_BIN_SLOT", "rack_id": "510019", "rack_face": "90", "slot_id": "S1"},
                target_json=TARGET,
                updated_at=NOW,
            )
        )
    return service, sessions, projections, provider


async def _close(service, **changes):
    args = {
        "transport_task_id": "manual-original",
        "bin_code": "A000000625",
        "target": HandoffPosition("CNV0401"),
        "expected_request_body_digest": DIGEST,
        "confirmed_by": "现场用户确认",
        "confirmation_reference": "user-confirmed-original-625",
    }
    args.update(changes)
    return await service.confirm_manual_bin_delivery(**args)


async def test_manual_completion_audits_atomically_and_preserves_identity_without_projection(manual_case):
    service, sessions, projections, provider = manual_case
    audit_id = await _close(service)
    assert await _close(service) == audit_id
    async with sessions() as db:
        task = await service._repository.get_task(db, "manual-original")
        member = (await service._repository.list_members(db, "manual-original"))[0]
        audit = await db.get(AuditLog, audit_id)
        assert task.status == member.status == "SUCCEEDED"
        assert task.reason_code == "MANUAL_TARGET_CONFIRMED" and task.outcome_version == 1
        assert task.last_applied_wms_outcome_revision == task.published_outcome_version == 0
        assert (
            task.client_request_id,
            task.submit_operation_id,
            task.submit_request_body,
            task.submit_request_body_digest,
        ) == ("manual-client", "manual-submit", BODY, DIGEST)
        assert member.final_position_json == TARGET and member.last_operation_id == f"manual:{audit_id}"
        assert audit.action == "MANUAL_TRANSPORT_COMPLETED" and audit.object_id == "manual-original"
        assert audit.args["confirmation"]["confirmed_by"] == "现场用户确认"
        assert audit.args["confirmation"]["confirmation_reference"] == "user-confirmed-original-625"
        assert audit.args["changes"]["projection_applied"] is False
        assert len((await db.scalars(select(AuditLog).where(AuditLog.object_id == "manual-original"))).all()) == 1
        assert (
            await db.scalar(
                select(TransportEvidence.id).where(TransportEvidence.transport_task_id == "manual-original")
            )
            is None
        )
        assert (
            await db.scalar(
                select(TransportCallbackReceipt.id).where(
                    TransportCallbackReceipt.message_json["data"]["transport_task_id"].as_string() == "manual-original"
                )
            )
            is None
        )
    projections.apply_transport_result.assert_not_awaited()
    provider.submit.assert_not_awaited()
    publisher = SimpleNamespace(publish=AsyncMock())
    assert await service.publish_pending_outcomes(1000, publisher) >= 1
    assert await service.publish_pending_outcomes(1000, publisher) == 0
    assert sum(call.args[0].transport_task_id == "manual-original" for call in publisher.publish.await_args_list) == 1


@pytest.mark.parametrize("change", ["actor", "reference", "digest", "target", "bin", "terminal", "kind", "member"])
async def test_manual_completion_fails_closed_without_audit_or_state_change(manual_case, change):
    service, sessions, _, _ = manual_case
    args = {}
    if change == "actor":
        args["confirmed_by"] = " "
    if change == "reference":
        args["confirmation_reference"] = ""
    if change == "digest":
        args["expected_request_body_digest"] = "b" * 64
    if change == "target":
        args["target"] = HandoffPosition("OTHER")
    if change == "bin":
        args["bin_code"] = "OTHER"
    async with sessions.begin() as db:
        task = await service._repository.get_task(db, "manual-original")
        member = (await service._repository.list_members(db, "manual-original"))[0]
        if change == "terminal":
            task.status = "FAILED"
        if change == "kind":
            task.kind = "RACK_MOVE"
        if change == "member":
            member.status = "FAILED"
    with pytest.raises(TransportContractError):
        await _close(service, **args)
    async with sessions() as db:
        task = await service._repository.get_task(db, "manual-original")
        assert task.status == ("FAILED" if change == "terminal" else "ACCEPTED")
        assert task.outcome_version == 0
        assert await db.scalar(select(AuditLog.id).where(AuditLog.object_id == "manual-original")) is None


async def test_manual_completion_rejects_repeat_confirmation_drift(manual_case):
    service, _, _, _ = manual_case
    await _close(service)
    with pytest.raises(TransportContractError):
        await _close(service, confirmation_reference="different-confirmation")


async def test_audit_failure_rolls_back_manual_completion(manual_case, monkeypatch):
    from src.app.sys.services.audit_service import audit_log_service

    service, sessions, _, _ = manual_case
    monkeypatch.setattr(audit_log_service, "create_audit_log", AsyncMock(side_effect=RuntimeError("audit unavailable")))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await _close(service)
    async with sessions() as db:
        task = await service._repository.get_task(db, "manual-original")
        assert task.status == "ACCEPTED" and task.outcome_version == 0
        assert (await service._repository.list_members(db, "manual-original"))[0].status == "PENDING"


@pytest.mark.parametrize("result", ["matching", "failed", "wrong_target"])
async def test_late_wms_result_is_preserved_after_manual_completion(manual_case, result):
    service, sessions, projections, _ = manual_case
    await _close(service)
    member_result = {"container_id": "A000000625", "status": "SUCCEEDED", "final_position": TARGET}
    if result == "failed":
        member_result.update(status="FAILED", failure_code="RCS_EXECUTION_FAILED")
    if result == "wrong_target":
        member_result["final_position"] = {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}
    await record_valid_callback(
        service,
        operation_id="019f0000-0000-7000-8000-000000000080",
        transport_task_id="manual-original",
        operation="transport.task.resulted@v1",
        timestamp=1,
        payload={"kind": "BIN_MOVE", "outcome_revision": 1, "results": [member_result]},
    )
    await service.process_pending_evidence(1)
    async with sessions() as db:
        task = await service._repository.get_task(db, "manual-original")
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.transport_task_id == "manual-original")
        )
        assert (
            task.status == "SUCCEEDED" and task.reason_code == "MANUAL_TARGET_CONFIRMED" and task.outcome_version == 1
        )
        assert task.last_applied_wms_outcome_revision == int(result == "matching")
        assert evidence.status == ("APPLIED" if result == "matching" else "CONFLICT")
        assert (await service._repository.list_members(db, "manual-original"))[0].last_operation_id.startswith(
            "manual:"
        )
    projections.apply_transport_result.assert_not_awaited()


async def test_manual_completion_is_excluded_from_projection_recovery(manual_case):
    service, sessions, _, _ = manual_case
    await _close(service)
    async with sessions() as db:
        # 即使没有当前投影或原binding，也不把人工历史完成当作投影缺口。
        assert not any(
            row[0] == "manual-original"
            for row in await service._repository.list_final_result_projection_candidates(db, limit=100)
        )
        assert not any(
            row[0] == "manual-original"
            for row in await service._repository.list_projection_recovery_orphans(db, limit=100)
        )


async def test_manual_completion_waits_for_existing_pending_result(manual_case):
    service, sessions, _, _ = manual_case
    await record_valid_callback(
        service,
        operation_id="019f0000-0000-7000-8000-000000000081",
        transport_task_id="manual-original",
        operation="transport.task.resulted@v1",
        timestamp=1,
        payload={
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [{"container_id": "A000000625", "status": "SUCCEEDED", "final_position": TARGET}],
        },
    )
    with pytest.raises(TransportContractError, match="pending result"):
        await _close(service)
    async with sessions() as db:
        assert (await service._repository.get_task(db, "manual-original")).status == "ACCEPTED"
        assert await db.scalar(select(AuditLog.id).where(AuditLog.object_id == "manual-original")) is None
