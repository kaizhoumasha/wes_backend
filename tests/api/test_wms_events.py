"""共享 WMS Event 唯一生产入口的 ASGI 合同。"""

from __future__ import annotations

import importlib
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.app.wms_adapter.inbound_wire import RECOVERY_OPERATION
from src.app.wms_adapter.transport_event_handler import (
    MAX_TRANSPORT_EVENT_BODY_BYTES,
    TransportEventResponse,
)

TRANSPORT_BODY = (
    b'{"operation_id":"01988ef1-4d2a-7000-8000-000000000001",'
    b'"operation":"transport.task.resulted@v1","timestamp":1,"data":{}}'
)


def _events_module() -> Any:
    try:
        return importlib.import_module("src.app.wms_adapter.v1.events")
    except ModuleNotFoundError:
        pytest.fail("WMS Transport events route 尚未实现", pytrace=False)


def _none_policy(module: Any) -> Any:
    return module.WmsInboundAuthPolicy()


def _route_app(
    module: Any,
    handler: AsyncMock,
    policy: object | None,
    *,
    publisher: object | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.transport_runtime = SimpleNamespace(handler=SimpleNamespace(handle=handler))
    app.state.transport_event_stream_service = publisher or SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app.state.wms_inbound_auth_policy = policy
    app.include_router(module.router, prefix="/api/v1/wms")
    return app


@pytest.mark.asyncio
async def test_oversized_stream_stops_at_the_boundary_before_auth_json_or_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _events_module()
    policy = _none_policy(module)

    def _auth_must_not_run(_policy: object) -> bool:
        raise AssertionError("oversized body must be rejected before authentication")

    monkeypatch.setattr(module, "_permits_wms_event_endpoint", _auth_must_not_run)
    handler = AsyncMock()
    app = _route_app(module, handler, policy)
    messages = [
        {"type": "http.request", "body": b"x" * MAX_TRANSPORT_EVENT_BODY_BYTES, "more_body": True},
        {"type": "http.request", "body": b"y", "more_body": True},
        {"type": "http.request", "body": b"trailing-must-not-be-read", "more_body": False},
    ]
    received = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal received
        received += 1
        return messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/wms/events",
            "raw_path": b"/api/v1/wms/events",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
        },
        receive,
        send,
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    assert start["status"] == 413
    assert body == b""
    assert received == 2
    handler.assert_not_awaited()


@pytest.mark.parametrize(
    ("ack_code", "http_status"),
    (("RECEIVED", 202), ("DUPLICATE", 200)),
)
def test_none_profile_forwards_exact_bytes_and_wakes_evidence_worker_after_persisted_ack(
    monkeypatch: pytest.MonkeyPatch,
    ack_code: str,
    http_status: int,
) -> None:
    module = _events_module()
    raw_body = TRANSPORT_BODY
    response_body = {
        "operation_id": "01988ef1-4d2a-7000-8000-000000000001",
        "code": ack_code,
        "timestamp": 1786435200000,
        "data": {"transport_task_id": "transport-1"},
    }
    handler = AsyncMock(return_value=TransportEventResponse(http_status=http_status, body=response_body))
    enqueue = MagicMock()
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", enqueue)
    app = _route_app(module, handler, _none_policy(module))

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == http_status
    assert response.json() == response_body
    handler.assert_awaited_once_with(raw_body)
    enqueue.assert_called_once_with()


@pytest.mark.parametrize(
    ("ack_code", "http_status", "expected_disposition"),
    (
        ("RECEIVED", 202, "RECEIVED"),
        ("DUPLICATE", 200, "DUPLICATE"),
        ("CONFLICT", 409, "CONFLICT"),
        ("REJECTED", 422, "REJECTED"),
        ("UNAVAILABLE", 503, "UNAVAILABLE"),
    ),
)
def test_transport_ingress_attempt_publishes_safe_disposition_without_changing_response(
    monkeypatch: pytest.MonkeyPatch,
    ack_code: str,
    http_status: int,
    expected_disposition: str,
) -> None:
    module = _events_module()
    operation_id = "01988ef1-4d2a-7000-8000-000000000001"
    raw_body = json.dumps(
        {
            "operation_id": operation_id,
            "operation": "transport.task.resulted@v1",
            "timestamp": 1,
            "data": {
                "transport_task_id": "transport-1",
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": "RACK-01",
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "LINE-01"},
                "arrival_face": "A",
            },
        },
        separators=(",", ":"),
    ).encode()
    response_data = {"transport_task_id": "transport-1"} if ack_code in {"RECEIVED", "DUPLICATE"} else {}
    response_body = {
        "operation_id": operation_id,
        "code": ack_code,
        "timestamp": 1786435200000,
        "data": response_data,
    }
    handler = AsyncMock(return_value=TransportEventResponse(http_status=http_status, body=response_body))
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", MagicMock())
    app = _route_app(module, handler, _none_policy(module), publisher=publisher)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == http_status
    assert response.json() == response_body
    publisher.publish_to.assert_awaited_once()
    channel, event_type, payload = publisher.publish_to.await_args.args
    assert (channel, event_type) == ("transport:evidence:stream", "transport_ingress.attempted")
    assert payload["operation_id"] == operation_id
    assert payload["transport_task_id"] == "transport-1"
    assert payload["kind"] == "RACK_MOVE"
    assert payload["outcome_revision"] == 1
    assert payload["disposition"] == expected_disposition
    assert payload["status_code"] == http_status
    assert payload["observed_body_bytes"] == len(raw_body)
    assert "data" not in payload
    assert "raw_body" not in payload


def test_transport_ingress_publisher_failure_does_not_change_persisted_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _events_module()
    response_body = {
        "operation_id": "01988ef1-4d2a-7000-8000-000000000001",
        "code": "RECEIVED",
        "timestamp": 1786435200000,
        "data": {"transport_task_id": "transport-1"},
    }
    handler = AsyncMock(return_value=TransportEventResponse(http_status=202, body=response_body))
    publisher = SimpleNamespace(publish_to=AsyncMock(side_effect=ConnectionError("redis unavailable")))
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", MagicMock())
    app = _route_app(module, handler, _none_policy(module), publisher=publisher)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 202
    assert response.json() == response_body


def test_transport_ingress_validation_rejection_is_published_without_reading_body() -> None:
    module = _events_module()
    handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, handler, _none_policy(module), publisher=publisher)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"secret", headers={"Content-Type": "text/plain"})

    assert response.status_code == 400
    publisher.publish_to.assert_awaited_once()
    payload = publisher.publish_to.await_args.args[2]
    assert payload["disposition"] == "REJECTED"
    assert payload["error_code"] == "INVALID_CONTENT_TYPE"
    assert payload["observed_body_bytes"] == 0
    assert payload["operation_id"] is None
    assert "secret" not in str(payload)


def test_transport_ingress_diagnostics_sanitize_overlong_identity_without_changing_ack() -> None:
    module = _events_module()
    handler = AsyncMock(return_value=TransportEventResponse(http_status=422, body={"code": "REJECTED"}))
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, handler, _none_policy(module), publisher=publisher)
    raw_body = json.dumps(
        {
            "operation_id": "o" * 37,
            "operation": "transport.task.resulted@v1",
            "data": {"transport_task_id": "t" * 81},
        }
    ).encode()

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    payload = publisher.publish_to.await_args.args[2]
    assert payload["operation_id"] is None
    assert payload["operation"] == "transport.task.resulted@v1"
    assert payload["transport_task_id"] is None


@pytest.mark.asyncio
async def test_persisted_ack_defers_evidence_wakeup_until_response_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _events_module()
    handler = AsyncMock(return_value=TransportEventResponse(http_status=202, body={"code": "RECEIVED"}))
    enqueue = MagicMock()
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", enqueue)
    app = _route_app(module, handler, _none_policy(module))
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": TRANSPORT_BODY, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/wms/events",
            "headers": [(b"content-type", b"application/json")],
            "app": app,
        },
        receive,
    )

    response = await module.receive_wms_event(request)

    assert response.status_code == 202
    enqueue.assert_not_called()
    assert response.background is not None

    await response.background()

    enqueue.assert_called_once_with()


def test_non_persisted_ack_does_not_wake_evidence_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _events_module()
    handler = AsyncMock(
        return_value=TransportEventResponse(
            http_status=409,
            body={
                "operation_id": "01988ef1-4d2a-7000-8000-000000000001",
                "code": "CONFLICT",
                "timestamp": 1786435200000,
                "data": {"reason_code": "EVIDENCE_IDENTITY_CONFLICT"},
            },
        )
    )
    enqueue = MagicMock()
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", enqueue)
    app = _route_app(module, handler, _none_policy(module))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 409
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({"Content-Type": "application/json"}, 202),
        ({"Content-Type": "application/json; charset=utf-8"}, 202),
        ({"Content-Type": "text/plain"}, 400),
        ({"Content-Type": "application/json; charset=gbk"}, 400),
        ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, 400),
    ],
)
def test_transport_event_route_enforces_json_utf8_identity_headers(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_status: int,
) -> None:
    module = _events_module()
    handler = AsyncMock(return_value=TransportEventResponse(http_status=202, body={"code": "RECEIVED"}))
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", MagicMock())
    app = _route_app(module, handler, _none_policy(module))

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=TRANSPORT_BODY, headers=headers)

    assert response.status_code == expected_status
    if expected_status == 400:
        assert response.content == b""
        handler.assert_not_awaited()
    else:
        handler.assert_awaited_once_with(TRANSPORT_BODY)


@pytest.mark.parametrize(
    "headers",
    [
        [("Content-Type", "application/json"), ("Content-Type", "text/plain")],
        [
            ("Content-Type", "application/json"),
            ("Content-Encoding", "identity"),
            ("Content-Encoding", "gzip"),
        ],
        [("Content-Type", 'application/json; charset=u"t"f-8')],
        [("Content-Type", "application/json; charset =utf-8")],
        [("Content-Type", "application/json; charset= utf-8")],
    ],
)
def test_transport_event_route_rejects_ambiguous_or_malformed_json_headers(
    headers: list[tuple[str, str]],
) -> None:
    module = _events_module()
    handler = AsyncMock(return_value=TransportEventResponse(http_status=202, body={"code": "RECEIVED"}))
    app = _route_app(module, handler, _none_policy(module))

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"{}", headers=headers)

    assert response.status_code == 400
    assert response.content == b""
    handler.assert_not_awaited()


def test_enqueue_failure_keeps_persisted_ack_and_emits_stable_event(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _events_module()
    response_body = {
        "operation_id": "01988ef1-4d2a-7000-8000-000000000001",
        "code": "RECEIVED",
        "timestamp": 1786435200000,
        "data": {"transport_task_id": "transport-1"},
    }
    handler = AsyncMock(return_value=TransportEventResponse(http_status=202, body=response_body))
    monkeypatch.setattr(
        module.task_queue_gateway,
        "enqueue_transport_evidence",
        MagicMock(side_effect=ConnectionError("broker unavailable")),
    )
    app = _route_app(module, handler, _none_policy(module))

    with caplog.at_level(logging.WARNING, logger=module.__name__), TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 202
    assert response.json() == response_body
    assert [getattr(record, "event", None) for record in caplog.records] == ["transport.evidence.enqueue_failed"]


@pytest.mark.parametrize(
    "policy",
    (
        None,
        SimpleNamespace(network_trust_mode="public_network"),
        SimpleNamespace(inbound_auth_scheme="HMAC_SHA256"),
    ),
)
def test_missing_or_unsupported_frozen_policy_fails_closed_before_handler(policy: object | None) -> None:
    module = _events_module()
    handler = AsyncMock()
    app = _route_app(module, handler, policy)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"{}", headers={"Content-Type": "application/json"})

    assert response.status_code == 401
    assert response.content == b""
    handler.assert_not_awaited()


def test_handler_empty_error_body_remains_empty() -> None:
    module = _events_module()
    handler = AsyncMock(return_value=TransportEventResponse(http_status=400, body={}))
    app = _route_app(module, handler, _none_policy(module))

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"not-json", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.content == b""


def test_missing_transport_runtime_returns_unavailable_ack_for_associated_request() -> None:
    module = _events_module()
    app = FastAPI()
    app.state.transport_runtime = None
    app.state.wms_inbound_auth_policy = _none_policy(module)
    app.include_router(module.router, prefix="/api/v1/wms")
    operation_id = "01988ef1-4d2a-7000-8000-000000000001"
    raw_body = (
        b'{"operation_id":"'
        + operation_id.encode()
        + b'","operation":"transport.task.member_position_changed@v1","timestamp":1,'
        b'"data":{"transport_task_id":"transport-1","container_id":"bin-1","milestone":"SOURCE_PICKED"}}'
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 503
    assert response.json() == {
        "operation_id": operation_id,
        "code": "UNAVAILABLE",
        "timestamp": response.json()["timestamp"],
        "data": {},
    }
    assert isinstance(response.json()["timestamp"], int)


def test_missing_transport_runtime_rejects_non_utf8_operation_before_association() -> None:
    module = _events_module()
    app = FastAPI()
    app.state.transport_runtime = None
    app.state.wms_inbound_auth_policy = _none_policy(module)
    app.include_router(module.router, prefix="/api/v1/wms")
    raw_body = (
        b'{"operation_id":"01988ef1-4d2a-7000-8000-000000000001",'
        rb'"operation":"\ud800","timestamp":1,"data":{}}'
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.content == b""


def test_missing_transport_runtime_rejects_nested_duplicate_key_before_association() -> None:
    module = _events_module()
    app = FastAPI()
    app.state.transport_runtime = None
    app.state.wms_inbound_auth_policy = _none_policy(module)
    app.include_router(module.router, prefix="/api/v1/wms")
    raw_body = (
        b'{"operation_id":"01988ef1-4d2a-7000-8000-000000000001",'
        b'"operation":"transport.task.member_position_changed@v1","timestamp":1,'
        b'"data":{"transport_task_id":"transport-1","container_id":"bin-1",'
        b'"milestone":"SOURCE_PICKED","milestone":"TARGET_PLACED"}}'
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.content == b""


@pytest.mark.parametrize(
    ("envelope", "expected_status"),
    (
        (
            {
                "operation_id": "01988ef1-4d2a-7000-8000-000000000002",
                "timestamp": 1,
                "data": {},
            },
            400,
        ),
        (
            {
                "operation_id": "01988ef1-4d2a-7000-8000-000000000002",
                "operation": "transport.task.unknown@v1",
                "timestamp": 1,
                "data": {},
            },
            422,
        ),
        (
            {
                "operation_id": "01988ef1-4d2a-7000-8000-000000000002",
                "operation": "transport.task.member_position_changed@v1",
                "timestamp": True,
                "data": {},
            },
            503,
        ),
        (
            {
                "operation_id": "01988ef1-4d2a-7000-8000-000000000002",
                "operation": "transport.task.member_position_changed@v1",
                "timestamp": 1,
                "data": {"transport_task_id": "transport-1"},
            },
            503,
        ),
    ),
)
def test_missing_transport_runtime_cannot_persist_associated_invalid_envelope(
    envelope: dict[str, Any], expected_status: int
) -> None:
    module = _events_module()
    app = FastAPI()
    app.state.transport_runtime = None
    app.state.wms_inbound_auth_policy = _none_policy(module)
    app.include_router(module.router, prefix="/api/v1/wms")

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", json=envelope)

    assert response.status_code == expected_status
    if expected_status == 400:
        assert response.content == b""
    elif expected_status == 503:
        assert response.json() == {
            "operation_id": envelope["operation_id"],
            "code": "UNAVAILABLE",
            "timestamp": response.json()["timestamp"],
            "data": {},
        }
        assert isinstance(response.json()["timestamp"], int)
    else:
        assert response.json()["operation_id"] == envelope["operation_id"]
        assert response.json()["code"] == "REJECTED"


def test_application_registers_exactly_one_shared_wms_events_route() -> None:
    from src import register

    app = FastAPI()
    register.register_routers(app)

    matches = [route for route in app.routes if getattr(route, "path", None) == "/api/v1/wms/events"]
    assert len(matches) == 1
    assert matches[0].methods == {"POST"}


def test_shared_wms_event_route_dispatches_recovery_to_the_exact_business_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    recovery_handler = AsyncMock(
        return_value=SimpleNamespace(
            http_status=202,
            body={
                "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
                "code": "RECEIVED",
                "timestamp": 2,
                "data": {},
            },
        )
    )
    enqueue = MagicMock()
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", enqueue)
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, transport_handler, _none_policy(module), publisher=publisher)
    app.state.wms_recovery_event_handler = SimpleNamespace(handle=recovery_handler)
    raw_body = json.dumps(
        {
            "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
            "operation": RECOVERY_OPERATION,
            "timestamp": 1,
            "data": {},
        }
    ).encode()

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 202
    recovery_handler.assert_awaited_once_with(raw_body)
    transport_handler.assert_not_awaited()
    publisher.publish_to.assert_not_awaited()
    enqueue.assert_not_called()


def test_shared_wms_event_route_rejects_unknown_operation_without_transport_fallback() -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    recovery_handler = AsyncMock()
    app = _route_app(module, transport_handler, _none_policy(module))
    app.state.wms_recovery_event_handler = SimpleNamespace(handle=recovery_handler)
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events",
            json={
                "operation_id": operation_id,
                "operation": "unknown.operation@v1",
                "timestamp": 1,
                "data": {},
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "REJECTED"
    assert response.json()["operation_id"] == operation_id
    transport_handler.assert_not_awaited()
    recovery_handler.assert_not_awaited()
