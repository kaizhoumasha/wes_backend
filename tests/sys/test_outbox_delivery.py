from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.sys.services.outbox_delivery import dispatch_external_http, dispatch_internal_signal


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

    with patch("src.celery_app.app.celery_app.send_task") as mock_send_task:
        result = await dispatch_internal_signal(outbox)

    assert result is True
    mock_send_task.assert_called_once_with(
        "src.celery_app.tasks.workline.process_signal", kwargs={"payload": {"k": "v"}}
    )


@pytest.mark.asyncio
async def test_dispatch_internal_signal_invalid_target() -> None:
    outbox = type("Outbox", (), {"target_code": "invalid_target"})()

    result = await dispatch_internal_signal(outbox)
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_internal_signal_celery_error() -> None:
    outbox = type("Outbox", (), {"target_code": "workline"})()

    with patch("src.celery_app.app.celery_app.send_task", side_effect=Exception("celery down")):
        result = await dispatch_internal_signal(outbox)

    assert result is False
