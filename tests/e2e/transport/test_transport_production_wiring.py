"""不依赖业务插件的 Transport 生产接线 E2E。"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select

from src.app.transport import BinMove, HandoffPosition, RackBinSlot, RackFace, TransportCaller, build_transport_runtime
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportResourceBinding,
    TransportTask,
)
from src.app.wms_adapter import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.app.wms_integration.provider_startup import assemble_wms_provider_startup
from src.celery_app.app import celery_app
from src.core.uuid7 import new_uuid7
from src.register import register_routers
from tests.contracts.wms_integration.provider_profile_support import (
    build_provider_profile_payload,
    write_provider_profile,
)
from tests.integration.conftest import (
    integration_engine,
    integration_guard,
    integration_session_factory,
    patch_global_session_factory,
)
from tests.support.transport_broker import (
    MockWmsHttpServer,
    TransportBrokerWorker,
    close_transport_test_resources,
)

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

SUBMIT_TASK = "src.celery_app.tasks.transport.submit_transport_tasks_batch"
EVIDENCE_TASK = "src.celery_app.tasks.transport.process_transport_evidence_batch"
RECONCILE_TASK = "src.celery_app.tasks.transport.reconcile_transport_tasks_batch"


async def _transport_counts(session_factory) -> tuple[int, int, int, int]:
    async with session_factory() as db:
        return (
            int(await db.scalar(select(func.count()).select_from(TransportTask)) or 0),
            int(await db.scalar(select(func.count()).select_from(TransportMember)) or 0),
            int(await db.scalar(select(func.count()).select_from(TransportEvidence)) or 0),
            int(await db.scalar(select(func.count()).select_from(TransportPositionProjection)) or 0),
        )


async def test_real_broker_route_worker_http_and_postgresql_converge_without_a_business_producer(
    tmp_path,
    integration_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_url = os.environ["INTEGRATION_REDIS_URL"]
    database_url = os.environ["INTEGRATION_DATABASE_URL"]
    task_id: str | None = None
    object_id: str | None = None
    evidence_operation_id: str | None = None
    server: MockWmsHttpServer | None = None
    runtime = None
    worker: TransportBrokerWorker | None = None
    success = False
    primary_error: BaseException | None = None

    async def _cleanup_database() -> None:
        if task_id is None or object_id is None:
            return
        async with integration_session_factory.begin() as db:
            if evidence_operation_id is not None:
                await db.execute(
                    delete(TransportCallbackReceipt).where(
                        TransportCallbackReceipt.operation_id == evidence_operation_id
                    )
                )
            await db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
            await db.execute(
                delete(TransportPositionProjection).where(TransportPositionProjection.object_id == object_id)
            )
            await db.execute(
                delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id == task_id)
            )
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == task_id))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))

    try:
        server = MockWmsHttpServer().start()
        payload = build_provider_profile_payload()
        payload["server_url"] = server.url
        profile_file = write_provider_profile(tmp_path / "provider.yaml", payload)
        startup = assemble_wms_provider_startup(SimpleNamespace(WMS_PROVIDER_PROFILE_FILE=profile_file))
        runtime = await build_transport_runtime(startup=startup, session_factory=integration_session_factory)
        worker = TransportBrokerWorker(database_url, redis_url, profile_file)
        worker.start()
        before_empty = await _transport_counts(integration_session_factory)
        assert worker.result(worker.send(SUBMIT_TASK, kwargs={"limit": 100})) == 0
        assert worker.result(worker.send(EVIDENCE_TASK, kwargs={"limit": 100})) == 0
        assert worker.result(worker.send(RECONCILE_TASK, kwargs={"limit": 100})) == 0
        assert await _transport_counts(integration_session_factory) == before_empty

        suffix = uuid.uuid4().hex
        object_id = f"bin-{suffix}"
        handle = await runtime.port.move_bins(
            new_uuid7(),
            TransportCaller("TRANSPORT_E2E"),
            (
                BinMove(
                    object_id,
                    RackBinSlot(f"rack-{suffix}", RackFace.A, "1"),
                    HandoffPosition(f"HANDOFF-{suffix}"),
                ),
            ),
        )
        task_id = handle.transport_task_id
        assert worker.result(worker.send(SUBMIT_TASK, kwargs={"limit": 100})) == 1
        server.wait_for_requests(1)

        async with integration_session_factory() as db:
            submitted = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        assert submitted is not None and submitted.status == "ACCEPTED"

        # Gateway 仍调用生产 Celery API；测试只把 Celery 传输连接指向显式 integration broker。
        monkeypatch.setattr(celery_app, "send_task", worker.producer.send_task)
        app = FastAPI()
        app.state.transport_runtime = runtime
        app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy.from_compiled_profile(startup.compiled_profile)
        register_routers(app)
        evidence_operation_id = new_uuid7()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://wes.test") as client:
            response = await client.post(
                "/api/v1/wms/events",
                json={
                    "operation_id": evidence_operation_id,
                    "operation": RESULT_OPERATION,
                    "timestamp": 1786435200000,
                    "data": {
                        "transport_task_id": task_id,
                        "kind": "BIN_MOVE",
                        "outcome_revision": 1,
                        "results": [
                            {
                                "container_id": object_id,
                                "status": "SUCCEEDED",
                                "final_position": {
                                    "kind": "HANDOFF_POSITION",
                                    "location_code": f"HANDOFF-{suffix}",
                                },
                            }
                        ],
                    },
                },
            )
        assert response.status_code == 202
        assert response.json()["code"] == "RECEIVED"

        deadline = asyncio.get_running_loop().time() + 15
        while True:
            async with integration_session_factory() as db:
                task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
                member = await db.scalar(select(TransportMember).where(TransportMember.transport_task_id == task_id))
                evidence = await db.scalar(
                    select(TransportEvidence).where(TransportEvidence.operation_id == evidence_operation_id)
                )
            if task is not None and task.status == "SUCCEEDED":
                break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"Transport evidence worker did not converge task; task={task!r}, evidence={evidence!r}")
            await asyncio.sleep(0.1)

        assert member is not None and member.status == "SUCCEEDED"
        assert member.final_position_json == {"kind": "HANDOFF_POSITION", "location_code": f"HANDOFF-{suffix}"}
        assert evidence is not None and evidence.status == "APPLIED"
        assert len(server.requests) == 1
        assert server.requests[0]["envelope"]["data"]["transport_task_id"] == task_id
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await close_transport_test_resources(
            worker=worker,
            runtime=runtime,
            server=server,
            cleanup_database=_cleanup_database,
            success=success,
            primary_error=primary_error,
        )
