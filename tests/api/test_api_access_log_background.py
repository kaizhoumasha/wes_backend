"""协议入口响应/SSE先于统一诊断数据库的 ASGI FAST owner。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.exc import DBAPIError

from src.app.device.contracts import DeviceEvidenceReceipt
from src.app.device.services.device_ingress_history_service import DeviceIngressHistoryService
from src.app.device.v1.ecs_callback import router as ecs_router
from src.app.sys.services.api_access_log_service import APIAccessLogService
from src.app.wms_adapter.callback_receipt_service import WmsCallbackReceiptService
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_event_handler import TransportEventResponse
from src.app.wms_adapter.v1 import events as wms_module
from src.app.wms_adapter.v1.events import router as wms_router
from src.core.error_handlers import register_exception_handlers
from src.utils.background_tasks import inject_background_tasks


@pytest.mark.parametrize("source", ["wms", "ecs", "ecs-error", "ecs-database-error"])
async def test_response_is_sent_and_live_event_published_before_blocked_diagnostic_write(source, monkeypatch):
    wakeup = Mock()
    monkeypatch.setattr(wms_module, "_enqueue_transport_evidence", wakeup)
    app = FastAPI(dependencies=[Depends(inject_background_tasks)])
    register_exception_handlers(app)
    logs = APIAccessLogService()
    entered = asyncio.Event()
    release = asyncio.Event()
    sent = []
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    entry_snapshot = []

    async def write(entry):
        assert any(message["type"] == "http.response.body" for message in sent)
        publisher.publish_to.assert_awaited_once()
        if source == "wms":
            wakeup.assert_called_once_with()
        entry_snapshot.append(entry)
        entered.set()
        await release.wait()
        raise RuntimeError("diagnostic database failed")

    logs.record = AsyncMock(side_effect=write)
    if source == "wms":
        app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
        app.state.wms_callback_receipt_service = WmsCallbackReceiptService(log_service=logs)
        app.state.transport_runtime = SimpleNamespace(
            handler=SimpleNamespace(handle=AsyncMock(return_value=TransportEventResponse(202, {"code": "RECEIVED"})))
        )
        app.state.transport_event_stream_service = publisher
        app.state.wms_event_stream_service = publisher
        app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
        app.include_router(wms_router, prefix="/api/v1/wms")
        path = "/api/v1/wms/events"
        payload = {
            "operation_id": "01988ef1-4d2a-7000-8000-000000000001",
            "operation": "transport.task.member_position_changed@v1",
            "timestamp": 1,
            "data": {"transport_task_id": "T", "container_id": "B", "milestone": "SOURCE_PICKED"},
        }
        expected_status = 202
    else:
        failure = (
            RuntimeError("business handler failed")
            if source == "ecs-error"
            else DBAPIError("select", {}, RuntimeError("lost"), connection_invalidated=True)
            if source == "ecs-database-error"
            else None
        )
        app.state.device_evidence_service = SimpleNamespace(
            accept_event=AsyncMock(
                return_value=DeviceEvidenceReceipt(2, "event-2", False, None, "PENDING"), side_effect=failure
            )
        )
        app.state.device_ingress_history_service = DeviceIngressHistoryService(log_service=logs)
        app.state.device_event_stream_service = publisher
        app.include_router(ecs_router, prefix="/api/v1/callback")
        path = "/api/v1/callback/event"
        payload = {"device_code": "ARM-01", "event_type": "SCAN_COMPLETED", "timestamp": 1786579204000, "data": {}}
        expected_status = 500 if source == "ecs-error" else 503 if source == "ecs-database-error" else 200
    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "scheme": "http",
        "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    call = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not call.done()
        assert sent[0]["status"] == expected_status
        assert entry_snapshot[0].status_code == expected_status
        assert entry_snapshot[0].direction == "INBOUND"
        if source == "wms":
            assert json.loads(sent[1]["body"]) == {"code": "RECEIVED"}
        release.set()
        await call
        assert sent[0]["status"] == expected_status
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
