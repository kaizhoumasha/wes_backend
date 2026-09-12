"""原 Beat 周期、真实 worker/HTTP 与独占持久化的 Transport 中断窗口。"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from celery.beat import Scheduler
from sqlalchemy import delete, update

from src.app.execution.models import InboundEvidence, PositionProjection
from src.app.transport.composition import build_transport_runtime
from src.app.transport.contracts import RackPosition, RackReference, RcsTemplateId, TransportCaller
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportTask,
)
from src.celery_app.config import beat_schedule
from src.core.task_queue_gateway import CeleryTaskQueueGateway
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.conftest import (
    integration_engine,
    integration_guard,
    integration_session_factory,
    patch_global_session_factory,
)
from tests.support.transport_broker import MockWmsHttpServer, TransportBrokerWorker, close_transport_test_resources

pytestmark = [pytest.mark.e2e, pytest.mark.integration, pytest.mark.asyncio]


def _worker(server):
    return TransportBrokerWorker(
        os.environ["INTEGRATION_DATABASE_URL"], os.environ["INTEGRATION_REDIS_URL"], server.url
    )


@asynccontextmanager
async def _case(sessions, monkeypatch, *, disconnect=False):
    server = MockWmsHttpServer(disconnect_after_receive=disconnect).start()
    runtime = await build_transport_runtime(
        wms_base_url=server.url, transport_submit_path="/submit", session_factory=sessions
    )
    case = SimpleNamespace(
        server=server,
        runtime=runtime,
        worker=_worker(server),
        task_id=None,
        rack=f"interrupt-{uuid4().hex[:12]}",
        callback_id=new_uuid7(),
    )

    def lost_wakeup(self, name, *, kwargs):
        raise ConnectionError("test broker publish unavailable")

    monkeypatch.setattr(CeleryTaskQueueGateway, "_send_task", lost_wakeup)

    async def cleanup_database():
        if case.task_id is None:
            return
        async with sessions.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == case.callback_id)
            )
            await db.execute(delete(InboundEvidence).where(InboundEvidence.transport_task_id == case.task_id))
            for model in (TransportEvidence, TransportMember):
                await db.execute(delete(model).where(model.transport_task_id == case.task_id))
            await db.execute(delete(PositionProjection).where(PositionProjection.object_id == case.rack))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == case.task_id))

    failure = None
    try:
        handle = await runtime.service.move_rack(
            new_uuid7(),
            TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
            case.rack,
            RackReference(case.rack),
            RackPosition("TEST-TARGET"),
            "90",
            RcsTemplateId.CTU01,
        )
        case.task_id = handle.transport_task_id
        yield case
    except BaseException as exc:
        failure = exc
    finally:
        await close_transport_test_resources(
            worker=case.worker,
            runtime=runtime,
            server=server,
            cleanup_database=cleanup_database,
            success=failure is None,
            primary_error=failure,
        )


async def _drive(case, predicate):
    # 真正执行 Celery Beat Scheduler，沿用生产周期；只选择本场景涉及的原扫描入口。
    case.worker.producer.conf.beat_schedule = {
        name: entry
        for name, entry in beat_schedule.items()
        if name
        in {
            "submit-transport-tasks-batch",
            "process-transport-evidence-batch",
            "publish-transport-outcomes-batch",
            "reconcile-transport-tasks-batch",
        }
    }
    assert len(case.worker.producer.conf.beat_schedule) == 4, "生产 Transport 扫描入口发生变化，需同步本测试"
    scheduler = Scheduler(app=case.worker.producer)
    try:
        async with asyncio.timeout(65):
            while True:
                snapshot = await case.runtime.service.get_task_snapshot(case.task_id)
                if predicate(snapshot):
                    return snapshot
                await asyncio.to_thread(scheduler.tick)
                await asyncio.sleep(0.05)
    finally:
        scheduler.close()


async def _callback(case, *, duplicate=False):
    message = {
        "operation": "transport.task.resulted@v1",
        "operation_id": case.callback_id,
        "timestamp": 1,
        "data": {
            "transport_task_id": case.task_id,
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": case.rack,
            "status": "SUCCEEDED",
            "arrival_face": "90",
            "final_position": {"kind": "RACK_POSITION", "location_code": "TEST-TARGET"},
        },
    }
    response = await case.runtime.handler.handle(json.dumps(message).encode())
    assert response.http_status == (200 if duplicate else 202)
    assert response.body["code"] == ("DUPLICATE" if duplicate else "RECEIVED")


@pytest.mark.parametrize("pending_stage", ["evidence", "outcome"])
async def test_original_work_survives_lost_wakeup_broker_data_and_consumer_restart(
    integration_session_factory, monkeypatch, pending_stage, record_property
):
    async with _case(integration_session_factory, monkeypatch) as case:
        original = await case.runtime.service.get_task_snapshot(case.task_id)
        assert original.send_started_at is None
        # 已入队的消息也可丢失；仅清除此独占 worker 的 broker/result 前缀。
        case.worker.send("src.celery_app.tasks.transport.submit_transport_tasks_batch")
        case.worker._cleanup_broker_artifacts()
        await asyncio.to_thread(case.worker.start)
        started = asyncio.get_running_loop().time()
        accepted = await _drive(case, lambda item: item.status == "ACCEPTED")
        record_property("submit_recovery_seconds", asyncio.get_running_loop().time() - started)
        assert accepted.submit_operation_id == original.submit_operation_id
        assert len(case.server.requests) == 1
        if pending_stage == "outcome":
            async with integration_session_factory.begin() as db:
                await db.execute(
                    update(TransportTask)
                    .where(TransportTask.transport_task_id == case.task_id)
                    .values(result_deadline_at=timezone.now_for_db() - timedelta(seconds=1))
                )
            unknown = await _drive(case, lambda item: item.status == "RECONCILING")
            assert unknown.reason_code == "TRANSPORT_RESULT_TIMEOUT"
        await asyncio.to_thread(case.worker.close, success=True)
        case.worker = None
        await _callback(case)
        pending = await case.runtime.service.get_task_snapshot(case.task_id)
        assert pending.pending_evidence_count == 1
        if pending_stage == "outcome":
            assert await case.runtime.service.process_pending_evidence(100) == 1
            pending = await case.runtime.service.get_task_snapshot(case.task_id)
            assert pending.status == "SUCCEEDED"
            assert pending.outcome_version > pending.published_outcome_version
        case.worker = _worker(case.server)
        await asyncio.to_thread(case.worker.start)
        completed = await _drive(
            case, lambda item: item.status == "SUCCEEDED" and item.outcome_version == item.published_outcome_version
        )
        assert completed.submit_operation_id == original.submit_operation_id
        assert len(case.server.requests) == 1
        duplicate = case.worker.send("src.celery_app.tasks.transport.publish_transport_outcomes_batch")
        assert await asyncio.to_thread(case.worker.result, duplicate) == 0
        await _callback(case, duplicate=True)
        after_duplicate = await case.runtime.service.get_task_snapshot(case.task_id)
        assert after_duplicate.outcome_version == completed.outcome_version
        assert after_duplicate.pending_evidence_count == 0


async def test_worker_exit_after_claim_never_resends_a_possible_physical_action(
    integration_session_factory, monkeypatch, tmp_path
):
    marker = tmp_path / "claimed-operation"
    async with _case(integration_session_factory, monkeypatch) as case:
        monkeypatch.setenv("TRANSPORT_TEST_CRASH_BEFORE_HTTP", str(marker))
        await asyncio.to_thread(case.worker.start)
        case.worker.send("src.celery_app.tasks.transport.submit_transport_tasks_batch")
        async with asyncio.timeout(20):
            while not marker.exists():  # noqa: ASYNC110 - 屏障由独立 worker 进程写入，不能用进程内 Event。
                await asyncio.sleep(0.05)
        pending = await case.runtime.service.get_task_snapshot(case.task_id)
        assert pending.send_started_at is not None
        assert marker.read_text() == pending.submit_operation_id
        assert not case.server.requests
        await asyncio.to_thread(case.worker.close, success=True)
        case.worker = None
        monkeypatch.delenv("TRANSPORT_TEST_CRASH_BEFORE_HTTP")
        async with integration_session_factory.begin() as db:
            await db.execute(
                update(TransportTask)
                .where(TransportTask.transport_task_id == case.task_id)
                .values(submit_claim_until=timezone.now_for_db() - timedelta(seconds=1))
            )
        case.worker = _worker(case.server)
        await asyncio.to_thread(case.worker.start)
        unknown = await _drive(case, lambda item: item.status == "RECONCILING")
        assert unknown.reason_code == "TRANSPORT_DELIVERY_UNKNOWN"
        assert unknown.submit_operation_id == pending.submit_operation_id
        assert unknown.submit_attempt_count == 1
        assert not case.server.requests


async def test_remote_receipt_then_connection_loss_preserves_identity_without_resend(
    integration_session_factory, monkeypatch
):
    async with _case(integration_session_factory, monkeypatch, disconnect=True) as case:
        await asyncio.to_thread(case.worker.start)
        unknown = await _drive(case, lambda item: item.status == "RECONCILING")
        assert len(case.server.requests) == 1
        assert unknown.reason_code == "TRANSPORT_DELIVERY_UNKNOWN"
        retry_scan = case.worker.send("src.celery_app.tasks.transport.submit_transport_tasks_batch")
        assert await asyncio.to_thread(case.worker.result, retry_scan) == 0
        assert len(case.server.requests) == 1
