"""Device ingress live-only SSE API 合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.device.contracts import DeviceIngressKind
from src.app.device.v1.evidence_stream import evidence_stream, router
from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus
from src.app.sys.services.event_stream_service import DEVICE_EVIDENCE_STREAM_CHANNEL
from src.core.rbac import require_superuser
from src.core.security import require_auth
from src.register import register_exception


def _attempt_payload(device_code: str = "ARM-01") -> dict[str, object]:
    return {
        "request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        "kind": "DEVICE_RESULT",
        "path": "/api/v1/callback/result",
        "received_at": "2026-08-23T08:00:00+00:00",
        "disposition": "ACCEPTED",
        "status_code": 200,
        "evidence_id": 1,
        "source_event_id": "RESULT:CMD-001",
        "device_code": device_code,
        "command_code": "CMD-001",
        "event_type": None,
        "apply_status": "PENDING",
        "error_code": None,
        "observed_body_bytes": 128,
        "raw_payload": {"command_code": "CMD-001", "device_code": device_code},
    }


def _update_payload(device_code: str = "ARM-01") -> dict[str, object]:
    return {
        "evidence_id": 1,
        "kind": "DEVICE_RESULT",
        "source_event_id": "RESULT:CMD-001",
        "device_code": device_code,
        "command_code": "CMD-001",
        "event_type": None,
        "apply_status": "APPLIED",
        "processed_at": "2026-08-23T08:00:01+00:00",
    }


class FakeStreamService:
    def __init__(self, events: tuple[dict[str, object], ...]) -> None:
        self.events = events
        self.subscriptions: list[tuple[str, float]] = []

    async def subscribe(self, channel: str, *, timeout_seconds: float):
        self.subscriptions.append((channel, timeout_seconds))
        for event in self.events:
            yield event


def test_device_evidence_stream_route_is_superuser_only() -> None:
    route = next(route for route in router.routes if isinstance(route, APIRoute) and route.path == "/evidences/stream")

    assert route.dependencies[0].dependency is require_superuser


@pytest.mark.asyncio
async def test_device_evidence_stream_rejects_missing_or_non_superuser_auth_and_ignores_query_token() -> None:
    app = FastAPI()
    register_exception(app)
    app.include_router(router, prefix="/api/v1/device")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/v1/device/evidences/stream?token=ignored")

    async def authenticate(request: Request) -> int:
        request.state.user_id = 42
        request.state.is_superuser = False
        return 42

    app.dependency_overrides[require_auth] = authenticate
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        forbidden = await client.get("/api/v1/device/evidences/stream")

    assert (missing.status_code, forbidden.status_code) == (401, 403)


@pytest.mark.asyncio
async def test_device_evidence_stream_uses_dedicated_channel_filters_and_omits_sse_id() -> None:
    stream = FakeStreamService(
        (
            {"type": "device_ingress.attempted", "payload": _attempt_payload("ARM-01"), "timestamp": 1},
            {"type": "device_ingress.attempted", "payload": _attempt_payload("ARM-02"), "timestamp": 2},
        )
    )
    app = FastAPI()
    app.state.device_event_stream_service = stream
    request = Request({"type": "http", "method": "GET", "path": "/stream", "headers": [], "app": app})

    response = await evidence_stream(
        request,
        device_code="ARM-02",
        kind=DeviceIngressKind.DEVICE_RESULT,
        command_code="CMD-001",
        apply_status=InboundEvidenceApplyStatus.PENDING,
    )
    body = response.body_iterator
    heartbeat = await anext(body)
    event = await anext(body)
    await body.aclose()

    assert heartbeat == ": heartbeat\n\n"
    assert "event: device_ingress.attempted\n" in event
    assert '"device_code": "ARM-02"' in event
    assert "id:" not in event
    assert stream.subscriptions == [(DEVICE_EVIDENCE_STREAM_CHANNEL, 25.0)]
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_device_evidence_stream_applies_the_same_filters_to_updates() -> None:
    stream = FakeStreamService(
        (
            {"type": "device_evidence.updated", "payload": _update_payload("ARM-01"), "timestamp": 1},
            {"type": "device_evidence.updated", "payload": _update_payload("ARM-02"), "timestamp": 2},
        )
    )
    app = FastAPI()
    app.state.device_event_stream_service = stream
    request = Request({"type": "http", "method": "GET", "path": "/stream", "headers": [], "app": app})

    response = await evidence_stream(
        request,
        device_code="ARM-02",
        kind=DeviceIngressKind.DEVICE_RESULT,
        command_code="CMD-001",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    body = response.body_iterator
    _ = await anext(body)
    event = await anext(body)
    await body.aclose()

    assert "event: device_evidence.updated\n" in event
    assert '"device_code": "ARM-02"' in event
    assert '"device_code": "ARM-01"' not in event
