from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload, EndpointDefinition, ExternalHttpDispatchRequest
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.services.outbox_delivery import dispatch_external_http, dispatch_internal_signal


class _FakeTaskQueueGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.internal_signals: list[tuple[str, dict[str, Any]]] = []

    def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("queue down")
        self.internal_signals.append((target_code, payload))

    def enqueue_workline_inbox(self, *, limit: int = 10) -> None:
        _ = limit

    def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None:
        _ = outbox_id, limit


@pytest.mark.asyncio
async def test_dispatch_external_http_success() -> None:
    canonical = CanonicalPayload.from_projection({"k": "v"})
    outbox = type(
        "Outbox",
        (),
        {
            "target_code": "TEST_ENDPOINT",
            "canonical_payload_bytes": canonical.body,
            "payload_hash": canonical.sha256,
        },
    )()
    registry = MagicMock()
    registry.resolve.return_value = EndpointDefinition(code="TEST_ENDPOINT", url="http://test/api")

    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )
    )

    result = await dispatch_external_http(outbox, registry, sender)
    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    request = sender.await_args.args[0]
    assert isinstance(request, ExternalHttpDispatchRequest)
    assert request.endpoint.url == "http://test/api"
    assert request.body == canonical.body


@pytest.mark.asyncio
async def test_dispatch_external_http_registry_failure() -> None:
    outbox = type("Outbox", (), {"target_code": "MISSING"})()
    registry = MagicMock()
    registry.resolve.side_effect = ValueError("Missing")
    sender = AsyncMock()

    result = await dispatch_external_http(outbox, registry, sender)
    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.safe_to_retry is False
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_external_http_sender_failure() -> None:
    canonical = CanonicalPayload.from_projection({"k": "v"})
    outbox = type(
        "Outbox",
        (),
        {
            "target_code": "TEST_ENDPOINT",
            "canonical_payload_bytes": canonical.body,
            "payload_hash": canonical.sha256,
        },
    )()
    registry = MagicMock()
    registry.resolve.return_value = EndpointDefinition(code="TEST_ENDPOINT", url="http://test/api")

    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
        )
    )

    result = await dispatch_external_http(outbox, registry, sender)
    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.safe_to_retry is True
    request = sender.await_args.args[0]
    assert isinstance(request, ExternalHttpDispatchRequest)
    assert request.body == canonical.body


@pytest.mark.asyncio
async def test_dispatch_internal_signal_success() -> None:
    outbox = type("Outbox", (), {"target_code": "workline", "payload_json": {"k": "v"}})()
    gateway = _FakeTaskQueueGateway()

    result = await dispatch_internal_signal(outbox, gateway)

    assert result is True
    assert gateway.internal_signals == [("workline", {"k": "v"})]


@pytest.mark.asyncio
async def test_dispatch_internal_signal_invalid_target() -> None:
    outbox = type("Outbox", (), {"target_code": "invalid_target"})()

    result = await dispatch_internal_signal(outbox)
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_internal_signal_celery_error() -> None:
    outbox = type("Outbox", (), {"target_code": "workline"})()

    result = await dispatch_internal_signal(outbox, _FakeTaskQueueGateway(fail=True))

    assert result is False
