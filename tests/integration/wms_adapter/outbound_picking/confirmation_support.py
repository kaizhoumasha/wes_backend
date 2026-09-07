"""Picking confirmation worker 测试共用装配，不承载插件业务。"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler

import pytest_asyncio
from sqlalchemy import delete

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskType
from src.app.workline.models import LineType, WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import migrated_database
from tests.support.transport_broker import MockWmsHttpServer, TransportBrokerWorker, close_transport_test_resources


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def confirmation_database(integration_guard):
    # 零插件 worker 的启动检查读取全部活动 WorkLine，不能继承其他领域的测试业务现场。
    async with migrated_database() as database:
        yield database


class _ConfirmationHandler(BaseHTTPRequestHandler):
    server: ConfirmationServer

    def do_POST(self) -> None:
        envelope = json.loads(self.rfile.read(int(self.headers["content-length"])))
        with self.server.condition:
            self.server.requests.append({"path": self.path, "envelope": envelope})
            unavailable = self.server.unavailable_first and len(self.server.requests) == 1
        payload = {
            "operation_id": envelope["operation_id"],
            "code": "UNAVAILABLE" if unavailable else self.server.code,
            "timestamp": int(time.time() * 1000),
            "data": {} if unavailable else self.server.data,
        }
        body = json.dumps(payload).encode()
        self.send_response(503 if unavailable else self.server.status_code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


class ConfirmationServer(MockWmsHttpServer):
    def __init__(self, *, status_code: int, code: str, data: dict, unavailable_first: bool = False) -> None:
        super().__init__()
        self.RequestHandlerClass = _ConfirmationHandler
        self.unavailable_first = unavailable_first
        self.status_code = status_code
        self.code = code
        self.data = data


async def seed_picking_owner(db, *, operation_id, issued_operation_id, task_key, now, status):
    workline = WorkLine(
        line_code=f"WMS-{operation_id[-12:]}",
        line_name="Prepare worker integration",
        line_type=LineType.MANUAL,
    )
    db.add(workline)
    await db.flush()
    workline_id = workline.id
    issued = InboundEvidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"outbound.picking_task.issued@v1:{issued_operation_id}",
        operation="outbound.picking_task.issued@v1",
        operation_id=issued_operation_id,
        payload_digest="c" * 64,
        normalized_payload={},
        received_at=now,
        processed_at=now,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    db.add(issued)
    await db.flush()
    issued_evidence_id = issued.id
    task = PickingTask(
        task_id=task_key,
        task_type=PickingTaskType.MANUAL,
        status=status,
        queue_revision=1,
        dispatch_sequence=1,
        issued_at_ms=1,
        issued_evidence_id=issued_evidence_id,
        workline_id=workline_id,
    )
    db.add(task)
    await db.flush()
    return task, workline


@asynccontextmanager
async def picking_confirmation_worker(database, *, server, status):
    """为一个 picking-owned 义务管理隔离 owner、HTTP server 和 worker 的生命周期。"""
    database_url, sessions = database
    operation_id, issued_operation_id = new_uuid7(), new_uuid7()
    now = timezone.now_for_db()
    worker = TransportBrokerWorker(
        database_url=database_url,
        redis_url=os.environ["INTEGRATION_REDIS_URL"],
        wms_base_url=server.url,
    )
    task = workline = None
    success = False
    primary_error = None

    async def cleanup_database():
        async with sessions.begin() as db:
            await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            if task is not None:
                await db.execute(delete(PickingTask).where(PickingTask.id == task.id))
            await db.execute(
                delete(InboundEvidence).where(InboundEvidence.operation_id.in_((operation_id, issued_operation_id)))
            )
            if workline is not None:
                await db.execute(delete(WorkLine).where(WorkLine.id == workline.id))

    try:
        async with sessions.begin() as db:
            task, workline = await seed_picking_owner(
                db,
                operation_id=operation_id,
                issued_operation_id=issued_operation_id,
                task_key=f"PICKING-WORKER-{operation_id}",
                now=now,
                status=status,
            )
        server.start()
        yield worker, task, workline, operation_id, now
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
