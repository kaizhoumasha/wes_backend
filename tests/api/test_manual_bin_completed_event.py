from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.outbound_picking.event_ack import EventAck
from src.app.wms_adapter.v1.events import router

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


def _app(handler: AsyncMock | None) -> FastAPI:
    app = FastAPI()
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.state.wms_manual_bin_completed_handler = SimpleNamespace(handle=handler) if handler is not None else None
    app.state.wms_event_stream_service = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app.state.wms_callback_receipt_service = SimpleNamespace(record=AsyncMock())
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
    app.include_router(router, prefix="/api/v1/wms")
    return app


def _body() -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "operation": "outbound.manual_bin.work_completed@v1",
        "timestamp": 1_788_390_000_000,
        "data": {
            "task_id": "PICK-001",
            "bin_code": "BIN-001",
            "result": "NORMAL",
            "completed_at": 1_788_389_999_000,
        },
    }


def test_shared_route_dispatches_manual_bin_completion_without_plugin() -> None:
    handler = AsyncMock(
        return_value=EventAck(
            202,
            {"operation_id": OPERATION_ID, "code": "RECEIVED", "timestamp": 123, "data": {}},
        )
    )
    app = _app(handler)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", json=_body())

    assert response.status_code == 202
    handler.assert_awaited_once_with(_body(), observation=None)


def test_shared_route_returns_503_when_manual_bin_receipt_runtime_is_missing() -> None:
    app = _app(None)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", json=_body())

    assert response.status_code == 503
    assert response.json()["operation_id"] == OPERATION_ID
    assert "outbound.manual_bin.work_completed@v1" in json.dumps(app.openapi())
