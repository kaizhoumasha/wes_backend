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
