from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.app.wms_adapter.return_rack_task_completion.handler import (
    ReturnRackTaskCompletedHandler,
    ReturnRackTaskCompletedPersistenceResult,
)
from src.app.wms_adapter.return_rack_task_completion.openapi import RETURN_RACK_TASK_COMPLETED_EXAMPLE


@pytest.mark.asyncio
async def test_static_asgi_route_receives_and_replays_committed_receipt(monkeypatch):
    from src.app.wms_adapter.v1 import events

    recorder = SimpleNamespace(
        record=AsyncMock(
            side_effect=[
                ReturnRackTaskCompletedPersistenceResult("RECEIVED", 123),
                ReturnRackTaskCompletedPersistenceResult("DUPLICATE", 124),
            ]
        )
    )
    from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy

    app = FastAPI()
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.include_router(events.router, prefix="/api/v1/wms")
    app.state.wms_return_rack_task_completed_handler = ReturnRackTaskCompletedHandler(recorder)
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None))
    monkeypatch.setattr(events, "_publish_wms_ingress_attempt", AsyncMock())
    monkeypatch.setattr(events, "wms_callback_receipt_service", SimpleNamespace(record=lambda **kwargs: None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/wms/events", json=RETURN_RACK_TASK_COMPLETED_EXAMPLE["value"])
        second = await client.post("/api/v1/wms/events", json=RETURN_RACK_TASK_COMPLETED_EXAMPLE["value"])
    assert (first.status_code, second.status_code) == (202, 200)
    assert first.json()["code"] == "RECEIVED" and second.json()["code"] == "DUPLICATE"
    assert recorder.record.await_count == 2
    schema = app.openapi()["paths"]["/api/v1/wms/events"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    item = next(
        s
        for s in schema["oneOf"]
        if s.get("properties", {}).get("operation", {}).get("const") == "outbound.return_rack.task_completed@v1"
    )
    assert item["additionalProperties"] is False
    assert item["properties"]["data"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_persistence_failure_cannot_ack_receipt():
    recorder = SimpleNamespace(record=AsyncMock(side_effect=RuntimeError("storage unavailable")))
    ack = await ReturnRackTaskCompletedHandler(recorder).handle(RETURN_RACK_TASK_COMPLETED_EXAMPLE["value"])
    assert ack.http_status == 503 and ack.body["code"] == "UNAVAILABLE"
