"""真实 worker、HTTP 与独占 PostgreSQL：不启动 Beat，不手动派发业务任务。"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.app.device.composition import DeviceEndpointAdapterProvider
from src.app.device.models.command import DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models import PositionProjection
from src.app.execution.models.inbound_evidence import InboundEvidence
from src.app.transport.composition import build_transport_runtime
from src.app.transport.contracts import RackPosition, RackReference, RcsTemplateId, TransportCaller
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.core.task_queue_gateway import CeleryTaskQueueGateway, task_queue_gateway
from src.core.uuid7 import new_uuid7
from tests.integration.conftest import (
    integration_engine,
    integration_guard,
    integration_session_factory,
    patch_global_session_factory,
)
from tests.support.ecs_uniform_wire import DeviceCommandBrokerWorker, UniformEcsServer, WesCallbackServer
from tests.support.transport_broker import MockWmsHttpServer, TransportBrokerWorker

pytestmark = [pytest.mark.e2e, pytest.mark.integration, pytest.mark.asyncio]


async def _wait_status(sessions, model, column, identity, status):
    async with asyncio.timeout(15):
        while True:
            async with sessions() as db:
                row = await db.scalar(select(model).where(column == identity))
                if row is not None and row.status == status:
                    return row
            await asyncio.sleep(0.05)


def _route_to_worker(monkeypatch, worker):
    results = []

    def send(self, name, *, kwargs):
        results.append(worker.producer.send_task(name, kwargs=kwargs))

    monkeypatch.setattr(CeleryTaskQueueGateway, "_send_task", send)
    return results


async def test_transport_create_and_callback_converge_without_beat(integration_session_factory, monkeypatch):
    server = MockWmsHttpServer().start()
    worker = TransportBrokerWorker(
        os.environ["INTEGRATION_DATABASE_URL"], os.environ["INTEGRATION_REDIS_URL"], server.url
    )
    runtime = None
    handle = None
    callback_operation_id = new_uuid7()
    success = False
    try:
        await asyncio.to_thread(worker.start)
        routed = _route_to_worker(monkeypatch, worker)
        runtime = await build_transport_runtime(
            wms_base_url=server.url,
            transport_submit_path="/api/WES/TransportRequests",
            session_factory=integration_session_factory,
        )
        rack = f"wake-rack-{uuid4().hex[:12]}"
        handle = await runtime.service.move_rack(
            new_uuid7(),
            TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
            rack,
            RackReference(rack),
            RackPosition("KT16"),
            "90",
            RcsTemplateId.CTU01,
        )
        await _wait_status(
            integration_session_factory,
            TransportTask,
            TransportTask.transport_task_id,
            handle.transport_task_id,
            "ACCEPTED",
        )
        # 重复唤醒也必须由数据库 claim/状态保护，只产生一次 HTTP 请求。
        task_queue_gateway.enqueue_transport_submit()
        await asyncio.sleep(0.2)
        assert len(server.requests) == 1
        # 独立验证 debug gateway 通道，由真实 broker/worker 完成一次空扫描。
        task_queue_gateway.enqueue_transport_debug()
        assert await asyncio.to_thread(worker.result, routed[-1]) == 0
        message = {
            "operation_id": callback_operation_id,
            "operation": "transport.task.resulted@v1",
            "timestamp": 1,
            "data": {
                "transport_task_id": handle.transport_task_id,
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": rack,
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "KT16"},
                "arrival_face": "90",
            },
        }

        response = await runtime.handler.handle(json.dumps(message).encode())
        assert response.http_status == 202
        await _wait_status(
            integration_session_factory,
            TransportTask,
            TransportTask.transport_task_id,
            handle.transport_task_id,
            "SUCCEEDED",
        )
        success = True
    finally:
        if runtime is not None:
            await runtime.aclose()
        await asyncio.to_thread(worker.close, success=success)
        server.close()
        if handle is not None:
            async with integration_session_factory.begin() as db:
                await db.execute(
                    delete(TransportCallbackReceipt).where(
                        TransportCallbackReceipt.operation_id == callback_operation_id
                    )
                )
                for model in (TransportEvidence, TransportResourceBinding, TransportMember):
                    await db.execute(delete(model).where(model.transport_task_id == handle.transport_task_id))
                await db.execute(delete(PositionProjection).where(PositionProjection.object_id == rack))
                await db.execute(
                    delete(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id)
                )


async def test_device_create_and_result_converge_without_beat(integration_session_factory, monkeypatch):
    worker = DeviceCommandBrokerWorker(
        database_url=os.environ["INTEGRATION_DATABASE_URL"], redis_url=os.environ["INTEGRATION_REDIS_URL"]
    )
    callback = WesCallbackServer(
        session_factory=integration_session_factory, task_queue_gateway=task_queue_gateway
    ).start()
    ecs = UniformEcsServer(callback_url=callback.result_url).start()
    provider = DeviceEndpointAdapterProvider(timeout_seconds=5)
    handle = None
    success = False
    try:
        await asyncio.to_thread(worker.start)
        _route_to_worker(monkeypatch, worker)
        service = DeviceCommandService(
            session_factory=integration_session_factory,
            adapter_provider=provider,
            task_queue_gateway=task_queue_gateway,
        )
        handle = await service.create_manual_debug_command(
            client_request_id=new_uuid7(),
            endpoint_base_url=ecs.url,
            device_code=f"ARM-WAKE-{uuid4().hex[:12]}",
            contract_key="arm.pick",
            contract_version="2.0",
            command_timeout_ms=30000,
            task_type="PICK",
            params={"source_location": "A", "target_location": "B"},
            trace_id="wake-test",
            execution_reason="主动唤醒测试",
            created_by=42,
        )
        await _wait_status(
            integration_session_factory, DeviceCommand, DeviceCommand.command_code, handle.command_code, "SUCCEEDED"
        )
        assert len(ecs.command_requests) == 1
        assert ecs.callback_errors == []
        success = True
    finally:
        await provider.aclose()
        await asyncio.to_thread(worker.close, success=success)
        ecs.close()
        callback.close()
        if handle is not None:
            async with integration_session_factory.begin() as db:
                await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code == handle.command_code))
                await db.execute(delete(InboundEvidence).where(InboundEvidence.command_code == handle.command_code))
