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

from src.app.wms_adapter.inbound_material.wire import RECOVERY_OPERATION
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_ISSUED_OPERATION
from src.app.wms_adapter.transport_event_handler import (
    TransportEventResponse,
)
from src.app.wms_adapter.wire_common import MAX_WMS_EVENT_BODY_BYTES

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
    app.state.wms_event_stream_service = app.state.transport_event_stream_service
    app.state.wms_callback_receipt_service = SimpleNamespace(record=AsyncMock())
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
    app.state.wms_inbound_auth_policy = policy
    app.include_router(module.router, prefix="/api/v1/wms")
    return app


def test_diagnostics_observes_final_ack_after_existing_receipt_failure() -> None:
    from src.app.wms_diagnostics.observation import WmsCallObservation

    module = _events_module()
    handler = AsyncMock(
        return_value=TransportEventResponse(
            202,
            {"operation_id": "01988ef1-4d2a-7000-8000-000000000001", "code": "RECEIVED", "timestamp": 1, "data": {}},
        )
    )
    app = _route_app(module, handler, _none_policy(module))
    observation = WmsCallObservation(direction="WMS_TO_WES")
    diagnostics = SimpleNamespace(start=AsyncMock(return_value=observation), finish=AsyncMock())
    app.state.wms_diagnostics_service = diagnostics
    app.state.wms_callback_receipt_service.record.side_effect = RuntimeError("receipt unavailable")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 503
    diagnostics.finish.assert_awaited_once_with(observation)
    assert observation.status_code == 503
    assert observation.response_body == response.content


def test_diagnostics_keeps_legal_rejection_separate_from_contract_error() -> None:
    from src.app.wms_diagnostics.observation import WmsCallObservation

    module = _events_module()
    handler = AsyncMock(
        return_value=TransportEventResponse(
            422,
            {
                "operation_id": "01988ef1-4d2a-7000-8000-000000000001",
                "code": "REJECTED",
                "timestamp": 1,
                "data": {"reason_code": "INVALID_EVIDENCE"},
            },
        )
    )
    app = _route_app(module, handler, _none_policy(module))
    observation = WmsCallObservation(direction="WMS_TO_WES")
    app.state.wms_diagnostics_service.start.return_value = observation
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422
    assert observation.result == "REJECTED"
    assert observation.error_code is None


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
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, handler, policy, publisher=publisher)
    messages = [
        {"type": "http.request", "body": b"x" * MAX_WMS_EVENT_BODY_BYTES, "more_body": True},
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
    channel, event_type, payload = publisher.publish_to.await_args.args
    assert (channel, event_type) == ("wms:inbound:stream", "wms_ingress.attempted")
    assert payload["error_code"] == "BODY_TOO_LARGE"
    assert payload["observed_body_bytes"] == MAX_WMS_EVENT_BODY_BYTES + 1


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
    handler.assert_awaited_once_with(raw_body, observation=None)
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


def test_wms_ingress_validation_rejection_is_published_without_transport_identity() -> None:
    module = _events_module()
    handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, handler, _none_policy(module), publisher=publisher)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"secret", headers={"Content-Type": "text/plain"})

    assert response.status_code == 400
    publisher.publish_to.assert_awaited_once()
    channel, event_type, payload = publisher.publish_to.await_args.args
    assert (channel, event_type) == ("wms:inbound:stream", "wms_ingress.attempted")
    assert payload["disposition"] == "REJECTED"
    assert payload["error_code"] == "INVALID_CONTENT_TYPE"
    assert payload["observed_body_bytes"] == 0
    assert "operation_id" not in payload
    assert "transport_task_id" not in payload
    assert "secret" not in str(payload)


def test_wms_ingress_publisher_failure_does_not_change_rejection() -> None:
    module = _events_module()
    handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(side_effect=ConnectionError("redis unavailable")))
    app = _route_app(module, handler, _none_policy(module), publisher=publisher)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"secret", headers={"Content-Type": "text/plain"})

    assert response.status_code == 400
    assert response.content == b""
    handler.assert_not_awaited()


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
        handler.assert_awaited_once_with(TRANSPORT_BODY, observation=None)


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
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, handler, policy, publisher=publisher)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"{}", headers={"Content-Type": "application/json"})

    assert response.status_code == 401
    assert response.content == b""
    handler.assert_not_awaited()
    channel, event_type, payload = publisher.publish_to.await_args.args
    assert (channel, event_type) == ("wms:inbound:stream", "wms_ingress.attempted")
    assert payload["error_code"] == "UNAUTHORIZED"
    assert payload["observed_body_bytes"] == 2


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
    app.state.wms_callback_receipt_service = SimpleNamespace(record=AsyncMock())
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
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
    app.state.wms_callback_receipt_service = SimpleNamespace(record=AsyncMock())
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
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
    app.state.wms_callback_receipt_service = SimpleNamespace(record=AsyncMock())
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
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
    app.state.wms_callback_receipt_service = SimpleNamespace(record=AsyncMock())
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
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
    from src.app.wms_diagnostics.observation import WmsCallObservation

    observation = WmsCallObservation(direction="WMS_TO_WES")
    app.state.wms_diagnostics_service.start.return_value = observation
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
    assert observation.request_schema is not None
    assert observation.request_validated is False
    assert observation.request_errors == ()
    transport_handler.assert_not_awaited()
    publisher.publish_to.assert_not_awaited()
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    ("http_status", "code", "data"),
    [
        (202, "RECEIVED", {}),
        (409, "CONFLICT", {"reason_code": "STATE_CONFLICT"}),
        (422, "REJECTED", {"reason_code": "INVALID_DATA"}),
    ],
)
def test_shared_wms_event_route_dispatches_picking_task_issued_to_the_exact_business_handler(
    http_status: int,
    code: str,
    data: dict[str, str],
) -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    recovery_handler = AsyncMock()
    issued_handler = AsyncMock(
        return_value=SimpleNamespace(
            http_status=http_status,
            body={
                "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
                "code": code,
                "timestamp": 1786060800123,
                "data": data,
            },
        )
    )
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, transport_handler, _none_policy(module), publisher=publisher)
    app.state.wms_recovery_event_handler = SimpleNamespace(handle=recovery_handler)
    app.state.wms_picking_task_issued_handler = SimpleNamespace(handle=issued_handler)
    raw_body = json.dumps(
        {
            "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
            "operation": PICKING_TASK_ISSUED_OPERATION,
            "timestamp": 1786060800000,
            "data": {
                "task_id": "PICK-20260811-001",
                "task_type": "MANUAL",
                "queue_revision": 1,
                "dispatch_sequence": 100,
            },
        }
    ).encode()

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == http_status
    assert response.json()["code"] == code
    assert response.json()["data"] == data
    issued_handler.assert_awaited_once_with(json.loads(raw_body), observation=None)
    recovery_handler.assert_not_awaited()
    transport_handler.assert_not_awaited()


@pytest.mark.parametrize("runtime_present", [True, False])
def test_issued_ingress_parses_json_once_with_real_handler(
    monkeypatch: pytest.MonkeyPatch,
    runtime_present: bool,
) -> None:
    from src.app.wms_adapter import strict_json
    from src.app.wms_adapter.outbound_picking.event_handler import (
        PickingTaskIssuedHandler,
        PickingTaskIssuedPersistenceResult,
    )

    module = _events_module()
    # Count the actual decoder, including aliases imported by route and handler.
    decode = MagicMock(wraps=strict_json.json.loads)
    monkeypatch.setattr(strict_json.json, "loads", decode)
    recorder = SimpleNamespace(record=AsyncMock(return_value=PickingTaskIssuedPersistenceResult("RECEIVED", 1)))
    app = _route_app(module, AsyncMock(), _none_policy(module))
    app.state.wms_picking_task_issued_handler = PickingTaskIssuedHandler(recorder) if runtime_present else None
    payload = {
        "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
        "operation": PICKING_TASK_ISSUED_OPERATION,
        "timestamp": 1786060800000,
        "data": {"task_id": "PICK-ONCE", "task_type": "MANUAL", "queue_revision": 1, "dispatch_sequence": 1},
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", json=payload)
    assert response.status_code == (202 if runtime_present else 503)
    assert decode.call_count == 1
    assert recorder.record.await_count == int(runtime_present)


@pytest.mark.parametrize(
    "body", [b"not-json", b'{"operation_id":"invalid","operation":"outbound.picking_task.issued@v1"}']
)
def test_issued_ingress_rejects_before_identity_without_calling_handler(body: bytes) -> None:
    from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedHandler

    module = _events_module()
    recorder = SimpleNamespace(record=AsyncMock())
    app = _route_app(module, AsyncMock(), _none_policy(module))
    app.state.wms_picking_task_issued_handler = PickingTaskIssuedHandler(recorder)
    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.content == b""
    recorder.record.assert_not_awaited()


def test_shared_wms_event_route_returns_unavailable_when_picking_task_runtime_is_missing() -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, transport_handler, _none_policy(module), publisher=publisher)
    operation_id = "019f33f0-58d7-7b4d-a23a-1b90aa5d4473"
    raw_body = json.dumps(
        {
            "operation_id": operation_id,
            "operation": PICKING_TASK_ISSUED_OPERATION,
            "timestamp": 1786060800000,
            "data": {
                "task_id": "PICK-20260811-001",
                "task_type": "MANUAL",
                "queue_revision": 1,
                "dispatch_sequence": 100,
            },
        }
    ).encode()

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 503
    assert response.json()["operation_id"] == operation_id
    assert response.json()["code"] == "UNAVAILABLE"
    transport_handler.assert_not_awaited()
    channel, event_type, payload = publisher.publish_to.await_args.args
    assert (channel, event_type) == ("wms:inbound:stream", "wms_ingress.attempted")
    assert payload["error_code"] == "PICKING_TASK_RUNTIME_UNAVAILABLE"


def test_shared_wms_event_route_rejects_unknown_operation_without_transport_fallback() -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    recovery_handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, transport_handler, _none_policy(module), publisher=publisher)
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
    channel, event_type, payload = publisher.publish_to.await_args.args
    assert (channel, event_type) == ("wms:inbound:stream", "wms_ingress.attempted")
    assert payload["disposition"] == "REJECTED"
    assert payload["error_code"] == "UNSUPPORTED_OPERATION"
    assert "operation_id" not in payload
    assert "transport_task_id" not in payload


def test_shared_wms_event_route_publishes_invalid_envelope_without_transport_identity() -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, transport_handler, _none_policy(module), publisher=publisher)

    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=b"not-json", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    payload = publisher.publish_to.await_args.args[2]
    assert payload["disposition"] == "REJECTED"
    assert payload["error_code"] == "INVALID_ENVELOPE"
    assert payload["observed_body_bytes"] == len(b"not-json")
    assert "operation_id" not in payload


def test_shared_wms_event_route_publishes_recovery_runtime_unavailable() -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    publisher = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app = _route_app(module, transport_handler, _none_policy(module), publisher=publisher)
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

    assert response.status_code == 503
    payload = publisher.publish_to.await_args.args[2]
    assert payload["disposition"] == "UNAVAILABLE"
    assert payload["error_code"] == "RECOVERY_RUNTIME_UNAVAILABLE"
    assert payload["observed_body_bytes"] == len(raw_body)
    assert "operation_id" not in payload


@pytest.mark.parametrize("runtime_present", [True, False])
@pytest.mark.parametrize("event_name", ["plan_delta", "queue_changed"])
def test_picking_task_route_is_static_and_independent_of_plugins(runtime_present: bool, event_name: str) -> None:
    module = _events_module()
    transport_handler = AsyncMock()
    app = _route_app(module, transport_handler, _none_policy(module))
    operation_id = "019f3400-0e17-7d2a-b944-3cf7953804da"
    payload = {
        "operation_id": operation_id,
        "operation": f"outbound.picking_task.{event_name}@v1",
        "timestamp": 1,
        "data": {},
    }
    handler = AsyncMock(
        return_value=TransportEventResponse(
            http_status=202, body={"operation_id": operation_id, "code": "RECEIVED", "timestamp": 2, "data": {}}
        )
    )
    setattr(
        app.state,
        f"wms_picking_task_{event_name}_handler",
        SimpleNamespace(handle=handler) if runtime_present else None,
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", json=payload)
    assert response.status_code == (202 if runtime_present else 503)
    assert response.json()["code"] == ("RECEIVED" if runtime_present else "UNAVAILABLE")
    if runtime_present:
        handler.assert_awaited_once_with(payload, observation=None)
    else:
        handler.assert_not_awaited()
    transport_handler.assert_not_awaited()
    assert f"outbound.picking_task.{event_name}@v1" in json.dumps(app.openapi())


@pytest.mark.parametrize("runtime_present", [True, False])
def test_queue_changed_ingress_parses_json_once_with_real_handler(
    monkeypatch: pytest.MonkeyPatch,
    runtime_present: bool,
) -> None:
    from src.app.wms_adapter import strict_json
    from src.app.wms_adapter.outbound_picking.queue_changed_event_handler import (
        PickingTaskQueueChangedHandler,
        PickingTaskQueueChangedPersistenceResult,
    )

    module = _events_module()
    decode = MagicMock(wraps=strict_json.json.loads)
    monkeypatch.setattr(strict_json.json, "loads", decode)
    recorder = SimpleNamespace(record=AsyncMock(return_value=PickingTaskQueueChangedPersistenceResult("RECEIVED", 1)))
    app = _route_app(module, AsyncMock(), _none_policy(module))
    app.state.wms_picking_task_queue_changed_handler = (
        PickingTaskQueueChangedHandler(recorder) if runtime_present else None
    )
    payload = {
        "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
        "operation": "outbound.picking_task.queue_changed@v1",
        "timestamp": 1,
        "data": {"task_id": "PICK-ONCE", "queue_revision": 2, "not_before": 0},
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", json=payload)
    assert response.status_code == (202 if runtime_present else 503)
    assert decode.call_count == 1
    assert recorder.record.await_count == int(runtime_present)
    if runtime_present:
        assert recorder.record.await_args.args[0].data.not_before == 0


@pytest.mark.parametrize(
    "status,body",
    [
        (202, {"code": "RECEIVED"}),
        (200, {"code": "DUPLICATE"}),
        (409, {"code": "CONFLICT", "data": {"reason_code": "EVIDENCE_IDENTITY_CONFLICT"}}),
        (422, {"code": "REJECTED"}),
        (503, {"code": "UNAVAILABLE"}),
    ],
)
def test_every_wms_response_is_receipted_before_return(
    monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]
) -> None:
    module = _events_module()
    monkeypatch.setattr(module.task_queue_gateway, "enqueue_transport_evidence", MagicMock())
    app = _route_app(module, AsyncMock(return_value=TransportEventResponse(status, body)), _none_policy(module))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events",
            content=TRANSPORT_BODY,
            headers={"Content-Type": "application/json", "X-Request-ID": "untrusted"},
        )
    assert response.status_code == status
    recorder = app.state.wms_callback_receipt_service.record
    recorder.assert_awaited_once()
    recorded = recorder.await_args.kwargs
    assert recorded["raw_body"] == TRANSPORT_BODY
    assert recorded["response_status"] == status
    assert json.loads(recorded["response_body"]) == body
    assert recorded["request_id"] != "untrusted"


@pytest.mark.parametrize(
    "content,headers,policy,status",
    [
        (b"not json", {"Content-Type": "application/json"}, True, 400),
        (b"\xff", {"Content-Type": "application/json"}, True, 400),
        (b"{}", {"Content-Type": "text/plain"}, True, 400),
        (b"x" * (MAX_WMS_EVENT_BODY_BYTES + 1), {"Content-Type": "application/json"}, True, 413),
        (TRANSPORT_BODY, {"Content-Type": "application/json"}, False, 401),
    ],
    ids=["invalid-json", "invalid-utf8", "invalid-content-type", "oversized", "unauthorized"],
)
def test_rejected_ingress_has_receipt_even_without_operation_identity(content, headers, policy, status) -> None:
    module = _events_module()
    app = _route_app(module, AsyncMock(), _none_policy(module) if policy else None)
    with TestClient(app) as client:
        response = client.post("/api/v1/wms/events", content=content, headers=headers)
    assert response.status_code == status
    recorder = app.state.wms_callback_receipt_service.record
    recorder.assert_awaited_once()
    assert recorder.await_args.kwargs["response_status"] == status
    assert recorder.await_args.kwargs["raw_body"] == content[: len(recorder.await_args.kwargs["raw_body"])]
    assert recorder.await_args.kwargs["raw_body"]


def test_unexpected_handler_failure_is_receipted() -> None:
    module = _events_module()
    app = _route_app(module, AsyncMock(side_effect=RuntimeError("handler failed")), _none_policy(module))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 500
    app.state.wms_callback_receipt_service.record.assert_awaited_once()
    assert app.state.wms_callback_receipt_service.record.await_args.kwargs["response_status"] == 500


def test_receipt_storage_failure_is_retryable_and_not_silently_acknowledged() -> None:
    module = _events_module()
    app = _route_app(
        module, AsyncMock(return_value=TransportEventResponse(202, {"code": "RECEIVED"})), _none_policy(module)
    )
    app.state.wms_callback_receipt_service.record.side_effect = RuntimeError("database unavailable")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


def test_handler_and_receipt_failure_returns_retryable_503() -> None:
    module = _events_module()
    app = _route_app(module, AsyncMock(side_effect=RuntimeError("handler database failed")), _none_policy(module))
    app.state.wms_callback_receipt_service.record.side_effect = RuntimeError("receipt database failed")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/wms/events", content=TRANSPORT_BODY, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["code"] == "UNAVAILABLE"
