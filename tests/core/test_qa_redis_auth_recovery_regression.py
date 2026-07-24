"""QA Redis 认证恢复回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core import security_runtime

# Regression: ISSUE-003 — 启动时 Redis 不可用后，认证操作必须按需重连
# Found by /qa on 2026-07-24
# Report: .gstack/qa-reports/qa-report-127-0-0-1-8011-2026-07-24.md


@pytest.mark.asyncio
async def test_access_token_creation_reconnects_after_startup_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = AsyncMock()
    available = False

    def is_available() -> bool:
        return available

    def get_client() -> AsyncMock | None:
        return redis_client if available else None

    async def reconnect() -> bool:
        nonlocal available
        available = True
        return True

    monkeypatch.setattr(security_runtime, "is_redis_available", is_available)
    monkeypatch.setattr(security_runtime, "get_redis", get_client)
    monkeypatch.setattr(security_runtime, "ensure_redis_connection", AsyncMock(side_effect=reconnect))

    token = await security_runtime.create_access_token(user_id=7)

    assert token.access_token
    security_runtime.ensure_redis_connection.assert_awaited_once_with()
    redis_client.setex.assert_awaited()
