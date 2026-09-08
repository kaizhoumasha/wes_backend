"""PickingTask 队列更新的原子持久化和并发 owner。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from src.app.execution.models import InboundEvidence, InboundEvidenceConflict
from src.app.wms_adapter.outbound_picking.queue_changed_wire import parse_picking_task_queue_changed_receipt
from src.app.wms_adapter.outbound_picking.wire import PickingTaskIssuedEvent
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.services.picking_task_issued import PickingTaskIssuedService
from src.app.wms_integration.outbound_picking.services.picking_task_queue_changed import PickingTaskQueueChangedService
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.uuid7 import new_uuid7
from tests.support.postgresql_heavy import run_alembic, temporary_database

NOW = datetime(2026, 9, 6)
OP = "outbound.picking_task.queue_changed@v1"
pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions():
    async with temporary_database() as (_name, url):
        run_alembic("upgrade", "head", database_url=url)
        engine = create_async_engine(url)
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()


async def issued(sessions, task_id="QUEUE-1", sequence=100):
    event = PickingTaskIssuedEvent.model_validate(
        {
            "operation_id": new_uuid7(),
            "operation": "outbound.picking_task.issued@v1",
            "timestamp": 1,
            "data": {
                "task_id": task_id,
                "task_type": "MANUAL",
                "queue_revision": 1,
                "dispatch_sequence": sequence,
                "not_before": 1000,
            },
        }
    )
    result = await PickingTaskIssuedService(sessions).record(event, received_at=NOW)
    assert result.code == "RECEIVED"


def changed(task_id="QUEUE-1", revision=2, **updates):
    return parse_picking_task_queue_changed_receipt(
        {
            "operation_id": new_uuid7(),
            "operation": OP,
            "timestamp": 2,
            "data": {"task_id": task_id, "queue_revision": revision, **updates},
        }
    )


@pytest.mark.asyncio
async def test_queue_update_preserves_omitted_values_and_replays_after_later_changes(sessions):
    await issued(sessions)
    service = PickingTaskQueueChangedService(sessions)
    first = changed(dispatch_sequence=90)
    result = await service.record(first, received_at=NOW)
    assert result.code == "RECEIVED"
    async with sessions() as db:
        task = await db.scalar(select(PickingTask))
        assert (task.queue_revision, task.dispatch_sequence, task.not_before_ms, task.version) == (2, 90, 1000, 1)
        evidence = await db.scalar(select(InboundEvidence).where(InboundEvidence.operation_id == first.operation_id))
        assert evidence.apply_status == "APPLIED"
    second = changed(revision=3, not_before=0)
    assert (await service.record(second, received_at=NOW)).code == "RECEIVED"
    duplicate = await service.record(first, received_at=datetime(2026, 9, 7))
    assert (duplicate.code, duplicate.timestamp_ms) == ("DUPLICATE", result.timestamp_ms)
    drift = first.model_copy(update={"data": first.data.model_copy(update={"dispatch_sequence": 91})})
    assert (await service.record(drift, received_at=NOW)).reason_code == "IDEMPOTENCY_CONFLICT"
    async with sessions() as db:
        task = await db.scalar(select(PickingTask))
        assert (task.queue_revision, task.dispatch_sequence, task.not_before_ms, task.version) == (3, 90, 0, 2)
        assert task.plan_blocked_evidence_id is None


@pytest.mark.asyncio
async def test_queue_rejections_are_durable_and_never_modify_task(sessions):
    service = PickingTaskQueueChangedService(sessions)
    missing = changed(dispatch_sequence=90)
    result = await service.record(missing, received_at=NOW)
    assert result.reason_code == "REFERENCE_CONFLICT"
    await issued(sessions)
    assert (await service.record(missing, received_at=NOW)).reason_code == "REFERENCE_CONFLICT"
    await issued(sessions, "OTHER", 200)
    for event, reason in [
        (changed(revision=4, dispatch_sequence=80), "REVISION_CONFLICT"),
        (changed(dispatch_sequence=200), "STATE_CONFLICT"),
        (changed(dispatch_sequence=100), "STATE_CONFLICT"),
    ]:
        rejected = await service.record(event, received_at=NOW)
        assert (rejected.code, rejected.reason_code) == ("CONFLICT", reason)
        assert await service.record(event, received_at=NOW) == rejected
    async with sessions() as db:
        task = await db.scalar(select(PickingTask).where(PickingTask.task_id == "QUEUE-1"))
        assert (task.queue_revision, task.dispatch_sequence, task.version) == (1, 100, 0)
        assert task.plan_blocked_evidence_id is None
        assert len((await db.scalars(select(InboundEvidenceConflict))).all()) == 4


@pytest.mark.asyncio
async def test_queue_invalid_data_is_saved_and_replayed(sessions):
    service = PickingTaskQueueChangedService(sessions)
    bad = changed(not_before=None)
    result = await service.record(bad, received_at=NOW)
    assert (result.code, result.reason_code) == ("REJECTED", "INVALID_DATA")
    assert await service.record(bad, received_at=NOW) == result
    async with sessions() as db:
        evidence = await db.scalar(select(InboundEvidence))
        assert evidence.apply_status == "IGNORED"
        assert evidence.normalized_payload == bad.raw_envelope


@pytest.mark.asyncio
async def test_same_revision_and_shared_sequence_are_serialized(sessions):
    await issued(sessions)
    await issued(sessions, "OTHER", 200)
    service = PickingTaskQueueChangedService(sessions)
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                service.record(event, received_at=NOW)
                for event in [changed(dispatch_sequence=90), changed(dispatch_sequence=80)]
            )
        ),
        5,
    )
    assert sorted(result.code for result in results) == ["CONFLICT", "RECEIVED"]
    assert next(result.reason_code for result in results if result.code == "CONFLICT") == "REVISION_CONFLICT"
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                service.record(event, received_at=NOW)
                for event in [changed(revision=3, dispatch_sequence=50), changed("OTHER", dispatch_sequence=50)]
            )
        ),
        5,
    )
    assert sorted(result.code for result in results) == ["CONFLICT", "RECEIVED"]
    assert next(result.reason_code for result in results if result.code == "CONFLICT") == "STATE_CONFLICT"


@pytest.mark.asyncio
async def test_queue_priority_swap_rejects_without_row_lock_deadlock(sessions):
    await issued(sessions)
    await issued(sessions, "OTHER", 200)
    service = PickingTaskQueueChangedService(sessions)
    results = await asyncio.wait_for(
        asyncio.gather(
            service.record(changed(dispatch_sequence=200), received_at=NOW),
            service.record(changed("OTHER", dispatch_sequence=100), received_at=NOW),
        ),
        5,
    )
    assert [result.reason_code for result in results] == ["STATE_CONFLICT", "STATE_CONFLICT"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PREPARING", "EXECUTING", "EXECUTION_COMPLETED"])
async def test_claimed_task_rejects_new_queue_update_but_replays_accepted_identity(sessions, status):
    await issued(sessions)
    service = PickingTaskQueueChangedService(sessions)
    accepted_event = changed(dispatch_sequence=90)
    accepted = await service.record(accepted_event, received_at=NOW)
    assert accepted.code == "RECEIVED"
    async with sessions.begin() as db:
        line = WorkLine(
            line_code="QUEUE-LINE",
            line_name="Queue test",
            line_type=LineType.MANUAL,
            run_mode=WorkLineRunMode.AUTO,
            is_active=True,
        )
        db.add(line)
        await db.flush()
        await db.flush()
        task = await db.scalar(select(PickingTask).with_for_update())
        task.status = status
        task.workline_id = line.id
    duplicate = await service.record(accepted_event, received_at=datetime(2026, 9, 7))
    assert (duplicate.code, duplicate.timestamp_ms) == ("DUPLICATE", accepted.timestamp_ms)
    rejected_event = changed(revision=3, dispatch_sequence=80)
    rejected = await service.record(rejected_event, received_at=NOW)
    assert (rejected.code, rejected.reason_code) == ("CONFLICT", "STATE_CONFLICT")
    assert await service.record(rejected_event, received_at=NOW) == rejected
    async with sessions() as db:
        task = await db.scalar(select(PickingTask))
        assert (task.status, task.queue_revision, task.dispatch_sequence, task.version) == (status, 2, 90, 1)
        assert task.plan_blocked_evidence_id is None
        evidence = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.operation_id == rejected_event.operation_id)
        )
        assert evidence.apply_status == "RECONCILING"


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_queue_and_evidence_and_same_request_can_retry(sessions):
    await issued(sessions)
    request = changed(dispatch_sequence=90, not_before=0)

    class RejectCommit(Session):
        pass

    @sqlalchemy_event.listens_for(RejectCommit, "before_commit")
    def reject_commit(session):
        session.flush()
        raise RuntimeError("queue commit rejected")

    failing_sessions = async_sessionmaker(sessions.kw["bind"], sync_session_class=RejectCommit)
    try:
        with pytest.raises(RuntimeError, match="queue commit rejected"):
            await PickingTaskQueueChangedService(failing_sessions).record(request, received_at=NOW)
    finally:
        sqlalchemy_event.remove(RejectCommit, "before_commit", reject_commit)
    async with sessions() as db:
        task = await db.scalar(select(PickingTask))
        assert (task.queue_revision, task.dispatch_sequence, task.not_before_ms, task.version) == (1, 100, 1000, 0)
        assert (
            await db.scalar(select(InboundEvidence.id).where(InboundEvidence.operation_id == request.operation_id))
            is None
        )
    result = await PickingTaskQueueChangedService(sessions).record(request, received_at=NOW)
    assert result.code == "RECEIVED"
    async with sessions() as db:
        task = await db.scalar(select(PickingTask))
        assert (task.queue_revision, task.dispatch_sequence, task.not_before_ms, task.version) == (2, 90, 0, 1)
        evidence = await db.scalar(select(InboundEvidence).where(InboundEvidence.operation_id == request.operation_id))
        assert evidence.apply_status == "APPLIED"


@pytest.mark.asyncio
async def test_public_route_commits_queue_update_without_any_plugin(sessions):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from src.app.wms_adapter import WmsInboundAuthPolicy
    from src.app.wms_adapter.callback_receipt_service import WmsCallbackReceiptService
    from src.app.wms_integration.outbound_picking.composition import build_outbound_picking_runtime
    from src.register import register_routers

    await issued(sessions)
    runtime = build_outbound_picking_runtime(session_factory=sessions)
    app = FastAPI()
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.state.wms_callback_receipt_service = WmsCallbackReceiptService(sessions)
    app.state.wms_picking_task_queue_changed_handler = runtime.picking_task_queue_changed_handler
    app.state.wms_event_stream_service = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    register_routers(app)
    payload = changed(not_before=0).model_dump(mode="json", exclude_none=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://wes.test") as client:
        received = await client.post("/api/v1/wms/events", json=payload)
        assert received.status_code == 202 and received.json()["code"] == "RECEIVED"
        async with sessions() as db:
            task = await db.scalar(select(PickingTask))
            assert (task.queue_revision, task.not_before_ms) == (2, 0)
        duplicate = await client.post("/api/v1/wms/events", json=payload)
        assert duplicate.status_code == 200 and duplicate.json()["code"] == "DUPLICATE"
        assert duplicate.json()["timestamp"] == received.json()["timestamp"]


@pytest.mark.asyncio
async def test_queue_update_and_issued_share_the_same_dispatch_sequence_fence(sessions):
    await issued(sessions)
    issuance = PickingTaskIssuedEvent.model_validate(
        {
            "operation_id": new_uuid7(),
            "operation": "outbound.picking_task.issued@v1",
            "timestamp": 1,
            "data": {"task_id": "COMPETING", "task_type": "MANUAL", "queue_revision": 1, "dispatch_sequence": 90},
        }
    )
    results = await asyncio.wait_for(
        asyncio.gather(
            PickingTaskQueueChangedService(sessions).record(changed(dispatch_sequence=90), received_at=NOW),
            PickingTaskIssuedService(sessions).record(issuance, received_at=NOW),
        ),
        5,
    )
    assert sorted(result.code for result in results) == ["CONFLICT", "RECEIVED"]
    assert next(result.reason_code for result in results if result.code == "CONFLICT") == "STATE_CONFLICT"
    async with sessions() as db:
        owners = (await db.scalars(select(PickingTask).where(PickingTask.dispatch_sequence == 90))).all()
        assert len(owners) == 1
