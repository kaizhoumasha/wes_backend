"""Transport evidence live-only SSE API 合同。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute

from src.app.transport.v1 import router


class FakeStreamService:
    def __init__(self, events: tuple[dict[str, object] | None, ...]) -> None:
        self.events = events
        self.subscriptions: list[tuple[str, float]] = []

    async def subscribe(self, channel: str, *, timeout_seconds: float):
        self.subscriptions.append((channel, timeout_seconds))
        for event in self.events:
            yield event


def _route(path: str) -> APIRoute:
    route = next(
        (item for item in router.routes if isinstance(item, APIRoute) and item.path == path),
        None,
    )
    assert route is not None
    return route


def _attempt_payload() -> dict[str, object]:
    return {
        "request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": "transport.task.resulted@v1",
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "received_at": "2026-08-28T08:00:00Z",
        "disposition": "RECEIVED",
        "status_code": 202,
        "error_code": None,
        "observed_body_bytes": 256,
    }


def test_transport_evidence_stream_uses_dedicated_permission_and_openapi_media_type() -> None:
    route = _route("/v1/transport/evidences/stream")

    assert [getattr(item.dependency, "permission_required", "") for item in route.dependencies] == [
        "ops:transport-evidence:stream"
    ]
    app = FastAPI()
    app.include_router(router, prefix="/api")
    response_content = app.openapi()["paths"]["/api/v1/transport/evidences/stream"]["get"]["responses"]["200"][
        "content"
    ]
    assert response_content == {"text/event-stream": {"schema": {"type": "string"}}}


@pytest.mark.asyncio
async def test_transport_evidence_stream_uses_dedicated_channel_skips_invalid_payload_and_omits_id() -> None:
    stream = FakeStreamService(
        (
            None,
            {"type": "unknown", "payload": {}, "timestamp": 1},
            {"type": "transport_ingress.attempted", "payload": {"raw_body": "secret"}, "timestamp": 2},
            {"type": "transport_ingress.attempted", "payload": _attempt_payload(), "timestamp": 3},
        )
    )
    app = FastAPI()
    app.state.transport_event_stream_service = stream
    request = Request({"type": "http", "method": "GET", "path": "/stream", "headers": [], "app": app})

    response = await _route("/v1/transport/evidences/stream").endpoint(request)
    body = response.body_iterator
    heartbeat = await anext(body)
    event = await anext(body)
    await body.aclose()

    assert stream.subscriptions == [("transport:evidence:stream", 25.0)]
    assert heartbeat == ": heartbeat\n\n"
    assert "event: transport_ingress.attempted\n" in event
    assert '"transport_task_id": "transport-1"' in event
    assert "raw_body" not in event
    assert "id:" not in event
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_transport_evidence_stream_accepts_only_closed_evidence_update_contract() -> None:
    payload = {
        "evidence_id": 11,
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": "transport.task.resulted@v1",
        "transport_task_id": "transport-1",
        "outcome_revision": 1,
        "status": "APPLIED",
        "conflict_code": None,
        "task_status": "FAILED",
        "reason_code": "TARGET_BLOCKED",
        "processed_at": "2026-08-28T08:00:01Z",
    }
    stream = FakeStreamService(({"type": "transport_evidence.updated", "payload": payload, "timestamp": 1},))
    app = FastAPI()
    app.state.transport_event_stream_service = stream
    request = Request({"type": "http", "method": "GET", "path": "/stream", "headers": [], "app": app})

    response = await _route("/v1/transport/evidences/stream").endpoint(request)
    event = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert "event: transport_evidence.updated\n" in event
    assert '"status": "APPLIED"' in event
    assert "payload_json" not in event
