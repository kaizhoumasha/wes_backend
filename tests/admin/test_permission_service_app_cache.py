from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.services.permission_service import get_app_permissions, invalidate_app_permissions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.database.redis_cache import RedisCache


class _FakeCache:
    def __init__(self) -> None:
        self.storage: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, int | None]] = []
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> object | None:
        return self.storage.get(key)

    async def set(self, key: str, value: object, expire: int | None = None) -> None:
        self.storage[key] = value
        self.set_calls.append((key, value, expire))

    async def delete(self, key: str) -> bool:
        self.deleted_keys.append(key)
        self.storage.pop(key, None)
        return True


class _FakeDBResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _FakeDB:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    async def execute(self, statement: object) -> _FakeDBResult:
        return _FakeDBResult(self.rows)


@pytest.mark.asyncio
async def test_get_app_permissions_caches_empty_set() -> None:
    cache = cast("RedisCache", _FakeCache())
    db = cast("AsyncSession", _FakeDB([]))

    permissions = await get_app_permissions(db, cache, 101)

    assert permissions == set()
    assert cache.set_calls == [(CacheKeys.app_permissions(101), [], CacheExpire.APP_PERMISSIONS_EMPTY)]


@pytest.mark.asyncio
async def test_invalidate_app_permissions_returns_redis_deletion_result() -> None:
    cache = cast("RedisCache", _FakeCache())
    cache.delete = AsyncMock(return_value=False)  # type: ignore[method-assign]

    assert await invalidate_app_permissions(cache, 101) is False
    cache.delete.assert_awaited_once_with(CacheKeys.app_permissions(101))


@pytest.mark.asyncio
async def test_get_app_permissions_deletes_invalid_cached_value() -> None:
    cache = cast("RedisCache", _FakeCache())
    cache.storage[CacheKeys.app_permissions(202)] = "{bad-json"
    db = cast("AsyncSession", _FakeDB([SimpleNamespace(name="device:command:send")]))

    permissions = await get_app_permissions(db, cache, 202)

    assert permissions == {"device:command:send"}
    assert cache.deleted_keys == [CacheKeys.app_permissions(202)]
    assert cache.set_calls[-1] == (
        CacheKeys.app_permissions(202),
        ["device:command:send"],
        CacheExpire.APP_PERMISSIONS,
    )


@pytest.mark.asyncio
async def test_get_app_permissions_rejects_mapping_payloads() -> None:
    cache = cast("RedisCache", _FakeCache())
    cache.storage[CacheKeys.app_permissions(203)] = {"*": True}
    db = cast("AsyncSession", _FakeDB([SimpleNamespace(name="device:command:send")]))

    permissions = await get_app_permissions(db, cache, 203)

    assert permissions == {"device:command:send"}
    assert cache.deleted_keys == [CacheKeys.app_permissions(203)]
    assert cache.set_calls[-1] == (
        CacheKeys.app_permissions(203),
        ["device:command:send"],
        CacheExpire.APP_PERMISSIONS,
    )
