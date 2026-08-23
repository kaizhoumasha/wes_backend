import asyncio
import importlib
import json
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_event_stream_service_publishes_to_redis_pubsub_channel() -> None:
    from src.app.sys.services.event_stream_service import SSE_EVENT_CHANNEL, EventStreamService

    redis_client = SimpleNamespace(publish=AsyncMock(return_value=1))
    service = EventStreamService()
    assert tuple(signature(service.publish).parameters) == ("event_type", "payload")

    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        published = await service.publish("device.status.changed", {"device_code": "ARM01"})

    assert published is True
    redis_client.publish.assert_awaited_once()
    channel, raw_event = redis_client.publish.await_args.args
    assert channel == SSE_EVENT_CHANNEL

    event = json.loads(raw_event)
    assert event["type"] == "device.status.changed"
    assert event["payload"] == {"device_code": "ARM01"}
    assert isinstance(event["timestamp"], int)


@pytest.mark.asyncio
async def test_event_stream_publish_failures_degrade_without_raising() -> None:
    from src.app.sys.services.event_stream_service import EventStreamService

    service = EventStreamService()
    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=None):
        assert await service.publish_to("device:evidence:stream", "event", {}) is False

    redis_client = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("redis down")))
    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        assert await service.publish_to("device:evidence:stream", "event", {}) is False


@pytest.mark.asyncio
async def test_event_stream_publish_timeout_degrades_without_blocking_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("src.app.sys.services.event_stream_service")

    async def never_publish(*_args: object) -> int:
        await asyncio.Event().wait()
        return 1

    monkeypatch.setattr(module, "SSE_PUBLISH_TIMEOUT_SECONDS", 0.01, raising=False)
    redis_client = SimpleNamespace(publish=AsyncMock(side_effect=never_publish))
    service = module.EventStreamService()

    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        published = await asyncio.wait_for(
            service.publish_to("device:evidence:stream", "device_ingress.attempted", {}),
            timeout=0.05,
        )

    assert published is False


@pytest.mark.asyncio
async def test_event_stream_service_publishes_to_explicit_channel_without_changing_default_publish() -> None:
    from src.app.sys.services.event_stream_service import EventStreamService

    redis_client = SimpleNamespace(publish=AsyncMock(return_value=1))
    service = EventStreamService()

    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        published = await service.publish_to(
            "device:evidence:stream",
            "device_ingress.attempted",
            {"request_id": "REQ-1"},
        )

    assert published is True
    channel, raw_event = redis_client.publish.await_args.args
    assert channel == "device:evidence:stream"
    assert json.loads(raw_event)["type"] == "device_ingress.attempted"


@pytest.mark.asyncio
async def test_event_stream_service_subscription_skips_malformed_message_and_cleans_up() -> None:
    from src.app.sys.services.event_stream_service import EventStreamService

    valid = json.dumps(
        {
            "type": "device_ingress.attempted",
            "payload": {"request_id": "REQ-1"},
            "timestamp": 1_787_475_600_000,
        }
    )
    pubsub = SimpleNamespace(
        subscribe=AsyncMock(),
        get_message=AsyncMock(
            side_effect=[
                None,
                {"type": "subscribe", "channel": b"device:evidence:stream", "data": 1},
                {"type": "message", "data": b"not-json"},
                {"type": "message", "data": valid.encode()},
            ]
        ),
        unsubscribe=AsyncMock(),
        aclose=AsyncMock(),
    )
    redis_client = SimpleNamespace(pubsub=lambda: pubsub)
    service = EventStreamService()

    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        subscription = service.subscribe("device:evidence:stream", timeout_seconds=0.01)
        assert await anext(subscription) is None
        assert pubsub.get_message.await_count == 2
        event = await anext(subscription)
        await subscription.aclose()

    assert event == {
        "type": "device_ingress.attempted",
        "payload": {"request_id": "REQ-1"},
        "timestamp": 1_787_475_600_000,
    }
    pubsub.subscribe.assert_awaited_once_with("device:evidence:stream")
    pubsub.unsubscribe.assert_awaited_once_with("device:evidence:stream")
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_stream_subscription_does_not_discard_first_live_message() -> None:
    from src.app.sys.services.event_stream_service import EventStreamService

    expected = {
        "type": "device_ingress.attempted",
        "payload": {"request_id": "REQ-FIRST"},
        "timestamp": 1_787_475_600_000,
    }
    pubsub = SimpleNamespace(
        subscribe=AsyncMock(),
        get_message=AsyncMock(
            side_effect=[
                {"type": "message", "data": json.dumps(expected).encode()},
                {"type": "subscribe", "channel": b"device:evidence:stream", "data": 1},
            ]
        ),
        unsubscribe=AsyncMock(),
        aclose=AsyncMock(),
    )
    redis_client = SimpleNamespace(pubsub=lambda: pubsub)
    service = EventStreamService()

    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        subscription = service.subscribe("device:evidence:stream", timeout_seconds=0.01)
        assert await anext(subscription) is None
        event = await anext(subscription)
        await subscription.aclose()

    assert event == expected


@pytest.mark.asyncio
async def test_event_stream_subscription_never_reports_ready_without_subscribe_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("src.app.sys.services.event_stream_service")
    monkeypatch.setattr(module, "SSE_SUBSCRIBE_TIMEOUT_SECONDS", 0.01)

    async def no_ack(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0)

    pubsub = SimpleNamespace(
        subscribe=AsyncMock(),
        get_message=AsyncMock(side_effect=no_ack),
        unsubscribe=AsyncMock(),
        aclose=AsyncMock(),
    )
    service = module.EventStreamService()

    redis_client = SimpleNamespace(pubsub=lambda: pubsub)
    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=redis_client):
        subscription = service.subscribe("device:evidence:stream", timeout_seconds=0.01)
        with pytest.raises(TimeoutError):
            await anext(subscription)

    pubsub.unsubscribe.assert_awaited_once_with("device:evidence:stream")
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_stream_subscription_timeout_covers_subscribe_command(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("src.app.sys.services.event_stream_service")
    monkeypatch.setattr(module, "SSE_SUBSCRIBE_TIMEOUT_SECONDS", 0.01)

    async def never_subscribe(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    pubsub = SimpleNamespace(
        subscribe=AsyncMock(side_effect=never_subscribe),
        get_message=AsyncMock(),
        unsubscribe=AsyncMock(),
        aclose=AsyncMock(),
    )
    service = module.EventStreamService()
    started_at = asyncio.get_running_loop().time()

    with patch(
        "src.app.sys.services.event_stream_service.get_redis",
        return_value=SimpleNamespace(pubsub=lambda: pubsub),
    ):
        subscription = service.subscribe("device:evidence:stream", timeout_seconds=0.01)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(subscription), timeout=0.2)

    assert asyncio.get_running_loop().time() - started_at < 0.1
    pubsub.get_message.assert_not_awaited()
    pubsub.unsubscribe.assert_awaited_once_with("device:evidence:stream")
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_stream_subscription_does_not_report_ready_without_redis() -> None:
    from src.app.sys.services.event_stream_service import EventStreamService

    with patch("src.app.sys.services.event_stream_service.get_redis", return_value=None):
        subscription = EventStreamService().subscribe("device:evidence:stream", timeout_seconds=0.01)
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)


@pytest.mark.asyncio
async def test_event_stream_subscription_cleanup_timeouts_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("src.app.sys.services.event_stream_service")
    monkeypatch.setattr(module, "SSE_CLEANUP_TIMEOUT_SECONDS", 0.01)

    async def never_finishes(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    pubsub = SimpleNamespace(
        subscribe=AsyncMock(),
        get_message=AsyncMock(return_value={"type": "subscribe", "channel": b"device:evidence:stream", "data": 1}),
        unsubscribe=AsyncMock(side_effect=never_finishes),
        aclose=AsyncMock(side_effect=never_finishes),
    )
    service = module.EventStreamService()

    with patch(
        "src.app.sys.services.event_stream_service.get_redis",
        return_value=SimpleNamespace(pubsub=lambda: pubsub),
    ):
        subscription = service.subscribe("device:evidence:stream", timeout_seconds=0.01)
        assert await anext(subscription) is None
        await asyncio.wait_for(subscription.aclose(), timeout=0.1)

    pubsub.unsubscribe.assert_awaited_once_with("device:evidence:stream")
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_deferred_sse_events_flushes_session_events_after_commit() -> None:
    from src.app.sys.services.event_stream_service import (
        DEVICE_STATUS_CHANGED_EVENT,
        defer_sse_event,
        publish_deferred_sse_events,
    )

    db = SimpleNamespace(info={})
    payload = {"device_code": "ARM01", "status": "RUNNING"}
    defer_sse_event(db, DEVICE_STATUS_CHANGED_EVENT, payload)

    with patch(
        "src.app.sys.services.event_stream_service.event_stream_service.publish",
        new=AsyncMock(return_value=True),
    ) as publish:
        await publish_deferred_sse_events(db)

    publish.assert_awaited_once_with(DEVICE_STATUS_CHANGED_EVENT, payload)

    with patch(
        "src.app.sys.services.event_stream_service.event_stream_service.publish",
        new=AsyncMock(return_value=True),
    ) as publish_again:
        await publish_deferred_sse_events(db)

    publish_again.assert_not_awaited()
