"""排空 operation 的真实 PostgreSQL / HTTP / worker 接入；复用公共测试运行时。

显式要求 RUN_WORKLINE_INTEGRATION=1、INTEGRATION_DATABASE_URL、INTEGRATION_REDIS_URL。
数据库 fixture 创建独占临时逻辑库，不能使用共享业务数据库作为测试现场。
"""

import os
from dataclasses import replace

import pytest
from sqlalchemy import select
from wes_plugin_sdk import wms_operations

from src.app.execution.models import InboundEvidence, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import WmsConfirmationService
from src.app.execution.services.wms_confirmation_service import WmsConfirmationIdentityConflictError
from src.app.wms_adapter.return_buffer_drain.typed import encode_request
from src.app.wms_integration.return_buffer_drain import (
    ReturnBufferDrainOwnerService,
    ReturnBufferDrainResultReader,
    ReturnBufferDrainScheduler,
)
from src.app.workline.models import LineType, WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
)
from tests.support.transport_broker import TransportBrokerWorker, close_transport_test_resources

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def _seed(db):
    line = WorkLine(
        line_code=f"DRAIN-{new_uuid7()[-12:]}",
        line_name="Drain",
        line_type=LineType.MANUAL,
        is_active=True,
        plugin_key="manual-picking",
    )
    db.add(line)
    await db.flush()
    intent = wms_operations.workline_return_buffer_drain_rack_decide(
        operation_id=new_uuid7(),
        workline_code=line.line_code,
        required_slot_count=1,
    )
    return line.id, intent


async def test_scheduler_persists_one_workline_obligation_and_conflicts_on_drift(confirmation_database):
    _, sessions = confirmation_database
    now = timezone.now_for_db()
    service = WmsConfirmationService(workline_owner=ReturnBufferDrainOwnerService(), session_factory=sessions)
    scheduler = ReturnBufferDrainScheduler(service)
    async with sessions.begin() as db:
        workline_id, intent = await _seed(db)
        await scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
    async with sessions.begin() as db:
        await scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
        rows = (
            await db.scalars(select(WmsConfirmation).where(WmsConfirmation.operation_id == intent.operation_id))
        ).all()
        assert len(rows) == 1
        assert rows[0].workline_id == workline_id
        assert rows[0].picking_task_id is None
        with pytest.raises(WmsConfirmationIdentityConflictError):
            await scheduler.create_in_session(
                db,
                replace(intent, required_slot_count=intent.required_slot_count + 1),
                workline_id=workline_id,
                created_at=now,
            )
    async with sessions() as db:
        original = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == intent.operation_id))
        assert original.status == WmsConfirmationStatus.RECONCILING
        assert original.request_payload["data"]["required_slot_count"] == intent.required_slot_count


@pytest.mark.parametrize("result", ["READY", "WAIT"])
async def test_zero_plugin_worker_dispatches_and_preserves_typed_result(confirmation_database, monkeypatch, result):
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    database_url, sessions = confirmation_database
    now = timezone.now_for_db()
    data = (
        {"result": "READY", "racks": [{"rack_id": "RACK-2", "rack_face": ["B"]}]}
        if result == "READY"
        else {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1000}
    )
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    worker = TransportBrokerWorker(
        database_url=database_url, redis_url=os.environ["INTEGRATION_REDIS_URL"], wms_base_url=server.url
    )
    scheduler = ReturnBufferDrainScheduler(WmsConfirmationService(workline_owner=ReturnBufferDrainOwnerService()))
    success, primary_error = False, None

    async def cleanup_database():
        pass

    try:
        async with sessions.begin() as db:
            workline_id, intent = await _seed(db)
            await scheduler.create_in_session(db, intent, workline_id=workline_id, created_at=now)
        server.start()
        worker.start()
        worker.result(worker.send("src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"))
        async with sessions.begin() as db:
            confirmation = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.operation_id == intent.operation_id)
            )
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == result
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            original, outcome = await ReturnBufferDrainResultReader().read(db, evidence, workline_id=workline_id)
            assert original == intent
            if result == "READY":
                assert outcome.result.racks[0].rack_id == "RACK-2"
                assert outcome.result.racks[0].rack_faces == ("B",)
            else:
                assert outcome.result.retry_after_ms == 1000
            assert server.requests == [
                {
                    "path": "/api/v1/wes/decisions",
                    "envelope": encode_request(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000)),
                }
            ]
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await close_transport_test_resources(
            worker=worker,
            runtime=None,
            server=server,
            cleanup_database=cleanup_database,
            success=success,
            primary_error=primary_error,
        )
