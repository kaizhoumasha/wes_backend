"""Celery 核心健康检查的 Redis 降级恢复合同。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    import pytest


class _SessionContext:
    def __init__(self) -> None:
        self.execute = AsyncMock()

    async def __aenter__(self) -> _SessionContext:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_health_check_reconnects_redis_inside_runtime_owner_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.celery_app.tasks import core as task_module

    db = _SessionContext()
    redis_client = MagicMock()
    redis_client.ping = AsyncMock(return_value=True)
    redis_client.dbsize = AsyncMock(return_value=7)
    redis_available = False
    runtime_loop: asyncio.AbstractEventLoop | None = None
    reconnect_loop: asyncio.AbstractEventLoop | None = None

    async def ensure_redis_connection() -> bool:
        nonlocal redis_available, reconnect_loop
        reconnect_loop = asyncio.get_running_loop()
        redis_available = True
        return True

    def run_async(factory: object) -> object:
        assert callable(factory)
        assert not asyncio.iscoroutine(factory)

        async def run_in_owner_loop() -> object:
            nonlocal runtime_loop
            runtime_loop = asyncio.get_running_loop()
            return await factory()  # type: ignore[operator]

        return asyncio.run(run_in_owner_loop())

    monkeypatch.setattr(task_module, "get_db_context", MagicMock(return_value=db))
    monkeypatch.setattr(task_module, "run_async", MagicMock(side_effect=run_async))
    monkeypatch.setattr(
        task_module, "ensure_redis_connection", AsyncMock(side_effect=ensure_redis_connection), raising=False
    )
    monkeypatch.setattr(task_module, "is_redis_available", lambda: redis_available)
    monkeypatch.setattr(task_module, "get_redis", lambda: redis_client if redis_available else None)
    monkeypatch.setattr(task_module, "_update_health_cache", MagicMock())

    result = task_module.health_check.run()

    assert result["status"] == "healthy"
    assert result["checks"]["redis"] == {"status": "connected", "db_size": 7}
    assert runtime_loop is reconnect_loop
    task_module.run_async.assert_called_once()
    task_module.ensure_redis_connection.assert_awaited_once_with()
    redis_client.ping.assert_awaited_once_with()
    redis_client.dbsize.assert_awaited_once_with()
