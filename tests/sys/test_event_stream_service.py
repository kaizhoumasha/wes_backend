import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_event_stream_service_publishes_to_redis_pubsub_channel() -> None:
    from src.app.sys.services.event_stream_service import SSE_EVENT_CHANNEL, EventStreamService

    redis_client = SimpleNamespace(publish=AsyncMock(return_value=1))
    service = EventStreamService()

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
