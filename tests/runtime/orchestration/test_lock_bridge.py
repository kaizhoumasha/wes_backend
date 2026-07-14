"""Redis 编排锁的故障降级合同。"""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.app.runtime.orchestration.lock_bridge import RedisDistributedLock


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_error", [RedisConnectionError("offline"), RedisTimeoutError("timeout")])
async def test_redis_connection_failures_fall_back_to_postgresql(redis_error: Exception) -> None:
    class _RedisStub:
        async def set(self, *_args: Any, **_kwargs: Any) -> bool:
            raise redis_error

    class _DbStub:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement: Any, _params: dict[str, Any]) -> None:
            self.statements.append(str(statement))

    db = _DbStub()
    lock = RedisDistributedLock(redis_client=_RedisStub(), max_retries=1)  # type: ignore[arg-type]

    async with lock.acquire("session:42", db=db):
        pass

    assert db.statements == ["SELECT pg_advisory_xact_lock(:resource_id)"]


@pytest.mark.asyncio
async def test_user_redis_error_is_not_misclassified_as_lock_failure() -> None:
    class _RedisStub:
        async def set(self, *_args: Any, **_kwargs: Any) -> bool:
            return True

        async def eval(self, *_args: Any, **_kwargs: Any) -> int:
            return 1

    lock = RedisDistributedLock(redis_client=_RedisStub(), max_retries=1)  # type: ignore[arg-type]

    with pytest.raises(RedisConnectionError, match="business redis failure"):
        async with lock.acquire("session:42"):
            raise RedisConnectionError("business redis failure")
