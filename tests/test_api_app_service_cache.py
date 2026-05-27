from __future__ import annotations

from copy import deepcopy
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.api_auth.constants import CacheKeys
from src.app.api_auth.models.api_application import APIApplication, AppStatus, AppType, ValidityPeriod
from src.app.api_auth.repositories.app_application_repository import APIAppRepository
from src.app.api_auth.services.app_service import APIAppService
from src.common.cache_config import cache_settings
from src.database.redis_cache import RedisCache

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeRedisCache(RedisCache):
    def __init__(self) -> None:
        super().__init__(redis=None, prefix="app")
        self.storage: dict[str, Any] = {}
        self.set_calls: list[tuple[str, Any, int | None, bool]] = []
        self.deleted_patterns: list[str] = []
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> Any | None:
        return self.storage.get(key)

    async def set(self, key: str, value: Any, expire: int | None = None, is_hot: bool = False) -> bool:
        self.storage[key] = value
        self.set_calls.append((key, value, expire, is_hot))
        return True

    async def delete(self, key: str) -> bool:
        self.deleted_keys.append(key)
        self.storage.pop(key, None)
        return True

    async def delete_pattern(self, pattern: str) -> int:
        self.deleted_patterns.append(pattern)
        keys_to_delete = [key for key in self.storage if fnmatch(key, pattern)]
        for key in keys_to_delete:
            del self.storage[key]
        return len(keys_to_delete)


class FakeAPIAppRepository:
    _model_name = "APIApplication"
    model = APIApplication

    def __init__(self) -> None:
        self.items: dict[int, APIApplication] = {}
        self.next_id = 1
        self.assigned_permissions: dict[int, list[int]] = {}
        self.update_payloads: list[dict[str, Any]] = []

    def add_hook(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def get_by_id(
        self, db: object, id: int, include_deleted: bool = False, **kwargs: Any
    ) -> APIApplication | None:
        app = self.items.get(id)
        if app is None:
            return None
        if app.is_deleted and not include_deleted:
            return None
        return deepcopy(app)

    async def create(self, db: object, data: dict[str, Any]) -> APIApplication:
        app = APIApplication(id=self.next_id, **data)
        self.items[self.next_id] = app
        self.next_id += 1
        return deepcopy(app)

    async def update(self, db: object, id: int, data: dict[str, Any]) -> APIApplication | None:
        app = self.items.get(id)
        if app is None:
            return None
        self.update_payloads.append(deepcopy(data))
        for field, value in data.items():
            if field == "version":
                continue
            setattr(app, field, value)
        app.increment_version()
        return deepcopy(app)

    async def delete(self, db: object, id: int) -> bool:
        app = self.items.get(id)
        if app is None or app.is_deleted:
            return False
        app.is_deleted = True
        return True

    async def soft_delete(self, db: object, id: int, deleted_by: int | None = None) -> APIApplication | None:
        app = self.items.get(id)
        if app is None:
            return None
        app.soft_delete(deleted_by)
        return deepcopy(app)

    async def restore(self, db: object, id: int) -> APIApplication | None:
        app = self.items.get(id)
        if app is None or not app.is_deleted:
            return None
        app.restore()
        return deepcopy(app)

    async def permanent_delete(self, db: object, id: int) -> bool:
        return self.items.pop(id, None) is not None

    async def assign_permissions(self, db: object, app_id: int, permission_ids: list[int]) -> None:
        self.assigned_permissions[app_id] = list(permission_ids)
        flush = getattr(db, "flush", None)
        if callable(flush):
            await flush()


def _build_service(repo: FakeAPIAppRepository) -> APIAppService:
    service = APIAppService()
    service.repo = cast("Any", repo)
    service._model_name = repo._model_name
    return service


def _db_session() -> AsyncSession:
    return cast("AsyncSession", object())


def _require_id(app: APIApplication) -> int:
    assert app.id is not None
    return app.id


async def _seed_app(repo: FakeAPIAppRepository) -> APIApplication:
    return await repo.create(
        object(),
        {
            "app_name": "Cache Test App",
            "app_type": AppType.ECS,
            "description": "cache test",
            "rate_limit_per_minute": 100,
            "rate_limit_per_hour": 5000,
            "validity_period": ValidityPeriod.ONE_YEAR,
            "app_id": "app_cache_test",
            "app_secret_encrypted": "encrypted-secret",
            "status": AppStatus.ACTIVE,
            "ip_whitelist": None,
            "expires_at": None,
        },
    )


@pytest.mark.asyncio
async def test_repository_assign_permissions_flushes_without_committing() -> None:
    repo = APIAppRepository(APIApplication)
    db = AsyncMock()

    await repo.assign_permissions(db, 7, [1, 2])

    assert db.execute.await_count == 2
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_by_app_id_caches_null_result() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()

    async def query(db: object, app_id: str) -> APIApplication | None:
        return None

    service._query_by_app_id = query  # type: ignore[method-assign]

    assert await service.get_by_app_id(db, cache, "missing-app-id") is None
    assert cache.set_calls[-1][:3] == (
        CacheKeys.app_by_app_id("missing-app-id"),
        "__BASE_SERVICE_NULL__",
        service.null_cache_expire,
    )


@pytest.mark.asyncio
async def test_create_invalidates_new_app_alias_null_cache() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = AsyncMock()
    new_alias = CacheKeys.app_by_app_id("app_reserved_key")
    cache.storage[new_alias] = "__BASE_SERVICE_NULL__"

    created = await service.create(
        db,
        {
            "app_name": "Created App",
            "app_type": AppType.ECS,
            "description": "cache test",
            "rate_limit_per_minute": 100,
            "rate_limit_per_hour": 5000,
            "validity_period": ValidityPeriod.ONE_YEAR,
            "app_id": "app_reserved_key",
            "app_secret_encrypted": "encrypted-secret",
            "status": AppStatus.ACTIVE,
            "ip_whitelist": None,
            "expires_at": None,
        },
        cache,
    )

    assert created is not None
    assert new_alias not in cache.storage
    assert new_alias in cache.deleted_keys
    assert f"{service.list_cache_prefix}:*" in cache.deleted_patterns


@pytest.mark.asyncio
async def test_update_invalidates_app_alias_cache() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    alias_key = CacheKeys.app_by_app_id(app.app_id)

    async def query(db: object, app_id: str) -> APIApplication | None:
        return next(
            (deepcopy(item) for item in repo.items.values() if item.app_id == app_id and not item.is_deleted),
            None,
        )

    service._query_by_app_id = query  # type: ignore[method-assign]

    cached = await service.get_by_app_id(db, cache, app.app_id)
    assert cached is not None
    assert alias_key in cache.storage
    assert cache.set_calls[-1][2] == cache_settings.API_APP.expire

    updated = await service.update(
        db,
        app_id,
        {"description": "updated", "version": app.version},
        cache,
    )

    assert updated is not None
    assert updated.description == "updated"
    assert alias_key not in cache.storage
    assert alias_key in cache.deleted_keys


@pytest.mark.asyncio
async def test_update_invalidates_old_and_new_app_alias_cache_when_app_id_changes() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    old_alias_key = CacheKeys.app_by_app_id(app.app_id)
    new_alias_key = CacheKeys.app_by_app_id("app_cache_new")
    cache.storage[old_alias_key] = app.model_dump(mode="json")
    cache.storage[new_alias_key] = "__BASE_SERVICE_NULL__"

    updated = await service.update(
        db,
        app_id,
        {"app_id": "app_cache_new", "version": app.version},
        cache,
    )

    assert updated is not None
    assert updated.app_id == "app_cache_new"
    assert old_alias_key not in cache.storage
    assert new_alias_key not in cache.storage
    assert old_alias_key in cache.deleted_keys
    assert new_alias_key in cache.deleted_keys


@pytest.mark.asyncio
async def test_reset_secret_invalidates_app_alias_cache() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    alias_key = CacheKeys.app_by_app_id(app.app_id)
    original_secret = app.app_secret_encrypted

    async def query(db: object, app_id: str) -> APIApplication | None:
        return next(
            (deepcopy(item) for item in repo.items.values() if item.app_id == app_id and not item.is_deleted),
            None,
        )

    service._query_by_app_id = query  # type: ignore[method-assign]

    _ = await service.get_by_app_id(db, cache, app.app_id)
    assert alias_key in cache.storage

    new_secret = await service.reset_secret(db, cache, app_id)

    assert new_secret.startswith("sec_")
    assert repo.update_payloads[-1]["version"] == app.version
    assert alias_key not in cache.storage

    refreshed = await service.get_by_app_id(db, cache, app.app_id)
    assert refreshed is not None
    assert refreshed.app_secret_encrypted != original_secret


@pytest.mark.asyncio
async def test_reset_validity_period_invalidates_all_app_caches() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    alias_key = CacheKeys.app_by_app_id(app.app_id)
    detail_key = f"{service.cache_prefix}:{app_id}:depth2:delFalse"
    list_key = f"{service.list_cache_prefix}:l10:o0:fmanual:smanual:d1:delFalse"

    repo.items[app_id].status = AppStatus.EXPIRED

    async def query(db: object, app_id: str) -> APIApplication | None:
        return next(
            (deepcopy(item) for item in repo.items.values() if item.app_id == app_id and not item.is_deleted),
            None,
        )

    service._query_by_app_id = query  # type: ignore[method-assign]

    _ = await service.get_by_id(db, cache, app_id)
    _ = await service.get_by_app_id(db, cache, app.app_id)
    cache.storage[list_key] = {"total": 1, "items": [app.model_dump(mode="json")]}

    assert detail_key in cache.storage
    assert alias_key in cache.storage
    assert list_key in cache.storage

    updated = await service.reset_validity_period(
        db,
        cache,
        app_id,
        ValidityPeriod.ONE_YEAR,
        app.version,
    )

    assert updated is not None
    assert updated.status == AppStatus.ACTIVE
    assert updated.expires_at == repo.items[app_id].created_at + ValidityPeriod.ONE_YEAR.to_timedelta()
    assert detail_key not in cache.storage
    assert alias_key not in cache.storage
    assert list_key not in cache.storage
    assert alias_key in cache.deleted_keys
    assert f"{service.cache_prefix}:{app_id}:*" in cache.deleted_patterns
    assert f"{service.list_cache_prefix}:*" in cache.deleted_patterns


@pytest.mark.asyncio
async def test_delete_and_restore_invalidate_app_alias_cache() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    alias_key = CacheKeys.app_by_app_id(app.app_id)

    async def query(db: object, app_id: str) -> APIApplication | None:
        return next(
            (deepcopy(item) for item in repo.items.values() if item.app_id == app_id and not item.is_deleted),
            None,
        )

    service._query_by_app_id = query  # type: ignore[method-assign]

    _ = await service.get_by_app_id(db, cache, app.app_id)
    assert alias_key in cache.storage

    deleted = await service.delete(db, app_id, cache)
    assert deleted is True
    assert alias_key not in cache.storage

    assert await service.get_by_app_id(db, cache, app.app_id) is None
    assert cache.storage[alias_key] == "__BASE_SERVICE_NULL__"

    restored = await service.restore(db, app_id, cache)
    assert restored is not None
    assert alias_key not in cache.storage

    visible = await service.get_by_app_id(db, cache, app.app_id)
    assert visible is not None
    assert visible.app_id == app.app_id


@pytest.mark.asyncio
async def test_assign_permissions_commits_and_invalidates_permission_cache() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = AsyncMock()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    permission_key = CacheKeys.app_permissions(app_id)
    cache.storage[permission_key] = ["api:callback:event"]

    await service.assign_permissions(db, cache, app_id, [11, 12])

    assert repo.assigned_permissions[app_id] == [11, 12]
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert f"{service.cache_prefix}:{app_id}:*" in cache.deleted_patterns
    assert permission_key not in cache.storage
    assert permission_key in cache.deleted_keys


@pytest.mark.asyncio
async def test_revoke_app_invalidates_permission_cache() -> None:
    repo = FakeAPIAppRepository()
    service = _build_service(repo)
    cache = FakeRedisCache()
    db = _db_session()
    app = await _seed_app(repo)
    app_id = _require_id(app)
    alias_key = CacheKeys.app_by_app_id(app.app_id)
    permission_key = CacheKeys.app_permissions(app_id)

    async def query(db: object, app_id: str) -> APIApplication | None:
        return next(
            (deepcopy(item) for item in repo.items.values() if item.app_id == app_id and not item.is_deleted),
            None,
        )

    service._query_by_app_id = query  # type: ignore[method-assign]

    _ = await service.get_by_app_id(db, cache, app.app_id)
    cache.storage[permission_key] = ["api:callback:event"]

    revoked = await service.revoke_app(db, app.app_id, cache)

    assert revoked is True
    assert repo.items[app_id].status == AppStatus.REVOKED
    assert alias_key not in cache.storage
    assert permission_key not in cache.storage
    assert alias_key in cache.deleted_keys
    assert permission_key in cache.deleted_keys
