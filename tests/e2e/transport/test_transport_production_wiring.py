"""不依赖业务插件的 Transport 生产接线 E2E。"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import delete, func, select

from src.app.execution.models import PositionProjection
from src.app.transport import build_transport_runtime
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.wms_adapter import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.celery_app.app import celery_app
from src.core.uuid7 import new_uuid7
from src.register import register_routers
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
from tests.support.transport_projections import confirm_rack_faces_with_sessions

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

SUBMIT_TASK = "src.celery_app.tasks.transport.submit_transport_tasks_batch"
DEBUG_RUN_TASK = "src.celery_app.tasks.transport.advance_transport_debug_runs_batch"
EVIDENCE_TASK = "src.celery_app.tasks.transport.process_transport_evidence_batch"
RECONCILE_TASK = "src.celery_app.tasks.transport.reconcile_transport_tasks_batch"
PUBLISH_TASK = "src.celery_app.tasks.transport.publish_transport_outcomes_batch"


async def _allow_permission() -> None:
    return None


def _allow_transport_permissions(app: FastAPI) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/transport/"):
            continue
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = _allow_permission


async def _transport_counts(session_factory) -> tuple[int, int, int, int]:
    async with session_factory() as db:
        return (
            int(await db.scalar(select(func.count()).select_from(TransportTask)) or 0),
            int(await db.scalar(select(func.count()).select_from(TransportMember)) or 0),
            int(await db.scalar(select(func.count()).select_from(TransportEvidence)) or 0),
            int(await db.scalar(select(func.count()).select_from(PositionProjection)) or 0),
        )


async def test_real_broker_route_worker_http_and_postgresql_converge_without_a_business_producer(
    integration_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_url = os.environ["INTEGRATION_REDIS_URL"]
    database_url = os.environ["INTEGRATION_DATABASE_URL"]
    task_id: str | None = None
    object_id: str | None = None
    rack_id: str | None = None
    evidence_operation_id: str | None = None
    server: MockWmsHttpServer | None = None
    runtime = None
    worker: TransportBrokerWorker | None = None
    success = False
    primary_error: BaseException | None = None

    async def _cleanup_database() -> None:
        if task_id is None and object_id is None and rack_id is None:
            return
        async with integration_session_factory.begin() as db:
            if evidence_operation_id is not None:
                await db.execute(
                    delete(TransportCallbackReceipt).where(
                        TransportCallbackReceipt.operation_id == evidence_operation_id
                    )
                )
            if task_id is not None:
                await db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
            projection_ids = [value for value in (object_id, rack_id) if value is not None]
            if projection_ids:
                await db.execute(delete(PositionProjection).where(PositionProjection.object_id.in_(projection_ids)))
            if task_id is not None:
                await db.execute(
                    delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id == task_id)
                )
                await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == task_id))
                await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))

    try:
        server = MockWmsHttpServer().start()
        runtime = await build_transport_runtime(
            wms_base_url=server.url,
            transport_submit_path="/api/WES/TransportRequests",
            session_factory=integration_session_factory,
        )
        worker = TransportBrokerWorker(database_url, redis_url, server.url, "/api/WES/TransportRequests")
        worker.start()
        before_empty = await _transport_counts(integration_session_factory)
        assert worker.result(worker.send(SUBMIT_TASK, kwargs={"limit": 100})) == 0
        assert worker.result(worker.send(DEBUG_RUN_TASK, kwargs={"limit": 100})) == 0
        assert worker.result(worker.send(EVIDENCE_TASK, kwargs={"limit": 100})) == 0
        assert worker.result(worker.send(RECONCILE_TASK, kwargs={"limit": 100})) == 0
        assert await _transport_counts(integration_session_factory) == before_empty

        suffix = uuid.uuid4().hex
        object_id = f"bin-{suffix}"
        rack_id = f"rack-{suffix}"
        await confirm_rack_faces_with_sessions(integration_session_factory, {rack_id: "90"})
        app = FastAPI()
        app.state.transport_runtime = runtime
        app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
        register_routers(app)
        _allow_transport_permissions(app)
        client_request_id = new_uuid7()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://wes.test") as client:
            created = await client.post(
                "/api/v1/transport/debug-tasks",
                json={
                    "client_request_id": client_request_id,
                    "station_id": "TRANSPORT-E2E",
                    "kind": "BIN_MOVE",
                    "data": {
                        "moves": [
                            {
                                "bin_id": object_id,
                                "source": {
                                    "kind": "RACK_BIN_SLOT",
                                    "rack_id": rack_id,
                                    "rack_face": "A",
                                    "slot_id": "1",
                                },
                                "target": {
                                    "kind": "HANDOFF_POSITION",
                                    "location_code": f"HANDOFF-{suffix}",
                                },
                            }
                        ]
                    },
                },
            )
            task_id = created.json()["data"]["transport_task_id"]
            pending = await client.get(f"/api/v1/transport/tasks/{task_id}")
        assert (created.status_code, created.json()["code"]) == (202, "1004")
        assert created.json()["data"] == {
            "transport_task_id": task_id,
            "client_request_id": client_request_id,
        }
        assert pending.json()["data"]["status"] == "PENDING"
        assert pending.json()["data"]["latest_evidence"] is None
        assert worker.result(worker.send(SUBMIT_TASK, kwargs={"limit": 100})) == 1
        server.wait_for_requests(1)

        async with integration_session_factory() as db:
            submitted = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        assert submitted is not None and submitted.status == "ACCEPTED"

        # Callback 仍调用生产 Celery API；测试先冻结 PENDING evidence，再显式驱动真实 worker。
        queued_tasks: list[str] = []

        def _record_task(name: str, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            queued_tasks.append(name)
            return SimpleNamespace(id="transport-e2e-recorded")

        monkeypatch.setattr(celery_app, "send_task", _record_task)
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
            callback_pending = await client.get(f"/api/v1/transport/tasks/{task_id}")
        assert response.status_code == 202
        assert response.json()["code"] == "RECEIVED"
        assert EVIDENCE_TASK in queued_tasks
        assert callback_pending.json()["data"]["status"] == "ACCEPTED"
        assert callback_pending.json()["data"]["latest_evidence"]["status"] == "PENDING"

        assert worker.result(worker.send(EVIDENCE_TASK, kwargs={"limit": 100})) == 1

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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://wes.test") as client:
            terminal = await client.get(f"/api/v1/transport/tasks/{task_id}")
        assert terminal.json()["data"]["status"] == "SUCCEEDED"
        assert terminal.json()["data"]["latest_evidence"]["status"] == "APPLIED"
        assert worker.result(worker.send(PUBLISH_TASK, kwargs={"limit": 100})) == 1
        assert worker.result(worker.send(PUBLISH_TASK, kwargs={"limit": 100})) == 0
        assert len(server.requests) == 1
        assert server.requests[0]["path"] == "/api/WES/TransportRequests"
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
