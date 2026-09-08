"""独占 Redis 上的近期记录保存、裁剪与游标验证。"""

import asyncio
import importlib
import json
import os
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.app.wms_diagnostics import repository as repository_module
from src.app.wms_diagnostics.config import DiagnosticsConfig
from src.app.wms_diagnostics.contracts import ExchangeObservation, ExchangeQuery
from src.app.wms_diagnostics.repository import DiagnosticsRepository
from src.utils.timezone import timezone


@pytest_asyncio.fixture
async def diagnostic_redis(integration_guard, monkeypatch):
    url = os.environ["INTEGRATION_REDIS_URL"]
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    key = f"test:wms-diagnostics:{uuid4().hex}"
    monkeypatch.setattr(repository_module, "STREAM_KEY", key)
    try:
        yield client, key
    finally:
        await client.delete(key)
        await client.aclose()


async def test_real_redis_bounds_count_retention_and_preserves_each_attempt(diagnostic_redis) -> None:
    redis, key = diagnostic_redis
    repo = DiagnosticsRepository(redis, DiagnosticsConfig(max_records=2))
    ids = []
    for number in range(3):
        event = ExchangeObservation(
            attempt_id=str(number),
            operation="sample@v1",
            operation_id="same-identity",
            observed_at=timezone.now_utc().isoformat(),
            direction="WES_TO_WMS",
        )
        ids.append(await repo.append(event.model_dump_json()))
    assert len(set(ids)) == 3
    assert await redis.xlen(key) == 2
    assert 0 < await redis.ttl(key) <= 86400
    page = await repo.list(ExchangeQuery())
    assert [row.attempt_id for row in page.items] == ["2", "1"]
    assert await repo.get(ids[0]) is None


async def test_real_redis_page_cursor_does_not_skip_prefetched_rows(diagnostic_redis) -> None:
    redis, _ = diagnostic_redis
    repo = DiagnosticsRepository(redis, DiagnosticsConfig())
    for number in range(7):
        await repo.append(
            ExchangeObservation(
                attempt_id=str(number), observed_at=timezone.now_utc().isoformat(), direction="WMS_TO_WES"
            ).model_dump_json()
        )
    cursor = None
    attempts = []
    for _ in range(5):
        page = await repo.list(ExchangeQuery(page_size=2, cursor=cursor))
        attempts.extend(row.attempt_id for row in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert attempts == ["6", "5", "4", "3", "2", "1", "0"]


async def test_real_redis_append_removes_expired_records(diagnostic_redis) -> None:
    redis, key = diagnostic_redis
    repo = DiagnosticsRepository(redis, DiagnosticsConfig())
    event = ExchangeObservation(attempt_id="new", observed_at=timezone.now_utc().isoformat(), direction="WES_TO_WMS")
    await redis.xadd(key, {"record": event.model_dump_json()}, id="1-0")
    await repo.append(event.model_dump_json())
    assert await redis.xlen(key) == 1


async def test_separate_process_completion_reaches_live_stream_and_history(diagnostic_redis, monkeypatch) -> None:
    from src.app.sys.services.event_stream_service import EventStreamService
    from src.app.wms_diagnostics.service import WmsDiagnosticsService

    redis, key = diagnostic_redis
    channel = f"{key}:live"
    stream_module = importlib.import_module("src.app.sys.services.event_stream_service")
    service_module = importlib.import_module("src.app.wms_diagnostics.service")
    monkeypatch.setattr(stream_module, "get_redis", lambda: redis)
    monkeypatch.setattr(service_module, "WMS_DIAGNOSTICS_CHANNEL", channel)
    service = WmsDiagnosticsService(
        DiagnosticsRepository(redis, DiagnosticsConfig()), EventStreamService(), DiagnosticsConfig()
    )
    stream = service.stream_events(ExchangeQuery())
    async with asyncio.timeout(10):
        assert await anext(stream) == ": heartbeat\n\n"
        program = """
import asyncio, importlib, os, sys
from redis.asyncio import Redis
from src.app.wms_diagnostics import repository as repository_module
from src.app.wms_diagnostics.config import DiagnosticsConfig
from src.app.wms_diagnostics.service import WmsDiagnosticsService
from src.app.sys.services.event_stream_service import EventStreamService

async def main():
    redis = Redis.from_url(os.environ["INTEGRATION_REDIS_URL"], decode_responses=True)
    importlib.import_module("src.app.sys.services.event_stream_service").get_redis = lambda: redis
    importlib.import_module("src.app.wms_diagnostics.service").WMS_DIAGNOSTICS_CHANNEL = sys.argv[2]
    repository_module.STREAM_KEY = sys.argv[1]
    config = DiagnosticsConfig()
    service = WmsDiagnosticsService(repository_module.DiagnosticsRepository(redis, config), EventStreamService(), config)
    try:
        observation = await service.start(direction="WES_TO_WMS", operation="sample@v1", operation_id="same-id")
        assert observation is not None
        observation.result = "RECEIVED"
        assert await service.finish(observation)
    finally:
        await redis.aclose()

asyncio.run(main())
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            program,
            key,
            channel,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            started = await anext(stream)
            completed = await anext(stream)
            assert "event: wms_exchange.started" in started
            assert "event: wms_exchange.completed" in completed
            event = json.loads(completed.split("data: ", 1)[1])
            assert event["result"] == "RECEIVED"
            detail = await service.get_exchange(event["exchange_id"])
            assert detail is not None and detail.attempt_id == event["attempt_id"]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            await stream.aclose()
    assert (await redis.pubsub_numsub(channel))[0][1] == 0


async def test_slow_sse_send_releases_real_redis_subscription(diagnostic_redis, monkeypatch) -> None:
    from src.app.sys.services.event_stream_service import EventStreamService
    from src.app.wms_diagnostics.service import WmsDiagnosticsService
    from src.core import sse

    redis, key = diagnostic_redis
    channel = f"{key}:slow"
    monkeypatch.setattr(
        importlib.import_module("src.app.sys.services.event_stream_service"), "get_redis", lambda: redis
    )
    monkeypatch.setattr(importlib.import_module("src.app.wms_diagnostics.service"), "WMS_DIAGNOSTICS_CHANNEL", channel)
    monkeypatch.setattr(sse, "SEND_TIMEOUT_SECONDS", 0.02)
    service = WmsDiagnosticsService(
        DiagnosticsRepository(redis, DiagnosticsConfig()), EventStreamService(), DiagnosticsConfig()
    )
    response = sse.BoundedStreamingResponse(service.stream_events(ExchangeQuery()), media_type="text/event-stream")

    async def send(message):
        if message["type"] == "http.response.body":
            assert (await redis.pubsub_numsub(channel))[0][1] == 1
            await asyncio.Event().wait()

    async with asyncio.timeout(5):
        with pytest.raises(TimeoutError):
            await response.stream_response(send)
    assert (await redis.pubsub_numsub(channel))[0][1] == 0
