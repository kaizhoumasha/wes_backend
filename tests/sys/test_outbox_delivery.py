from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    outbox = type("Outbox", (), {"target_code": "TEST_ENDPOINT", "payload_json": {"k": "v"}})()
    registry = MagicMock()
    registry.resolve.return_value = type("Endpoint", (), {"url": "http://test/api"})()

    sender = AsyncMock(return_value=True)

    result = await dispatch_external_http(outbox, registry, sender)
    assert result is True
    sender.assert_awaited_once_with("http://test/api", {"k": "v"})


@pytest.mark.asyncio
async def test_dispatch_external_http_registry_failure() -> None:
    outbox = type("Outbox", (), {"target_code": "MISSING"})()
    registry = MagicMock()
    registry.resolve.side_effect = ValueError("Missing")
    sender = AsyncMock()

    result = await dispatch_external_http(outbox, registry, sender)
    assert result is False
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_external_http_sender_failure() -> None:
    outbox = type("Outbox", (), {"target_code": "TEST_ENDPOINT", "payload_json": {"k": "v"}})()
    registry = MagicMock()
    registry.resolve.return_value = type("Endpoint", (), {"url": "http://test/api"})()

    sender = AsyncMock(return_value=False)

    result = await dispatch_external_http(outbox, registry, sender)
    assert result is False
    sender.assert_awaited_once_with("http://test/api", {"k": "v"})


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
