from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.admin.services.perm_service import PermissionService
from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.services.permission_service import get_app_permissions
from src.core.base_service import BaseService
from src.core.tree_service import TreeServiceMixin
from src.database.base_repository import HookContext


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

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)
        self.storage.pop(key, None)


class _FakeDBResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _FakeDB:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.execute_calls = 0

    async def execute(self, statement: object) -> _FakeDBResult:
        self.execute_calls += 1
        return _FakeDBResult(self.rows)


class _FakeRepo:
    _model_name = "Permission"

    def add_hook(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_get_app_permissions_caches_empty_set() -> None:
    cache = _FakeCache()
    db = _FakeDB([])

    permissions = await get_app_permissions(db, cache, 101)

    assert permissions == set()
    assert cache.set_calls == [
        (
            CacheKeys.app_permissions(101),
            [],
            CacheExpire.APP_PERMISSIONS_EMPTY,
        )
    ]


@pytest.mark.asyncio
async def test_get_app_permissions_deletes_invalid_cached_value() -> None:
    cache = _FakeCache()
    cache.storage[CacheKeys.app_permissions(202)] = "{bad-json"
    db = _FakeDB([SimpleNamespace(name="device:command:send")])

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
    cache = _FakeCache()
    cache.storage[CacheKeys.app_permissions(203)] = {"*": True}
    db = _FakeDB([SimpleNamespace(name="device:command:send")])

    permissions = await get_app_permissions(db, cache, 203)

    assert permissions == {"device:command:send"}
    assert cache.deleted_keys == [CacheKeys.app_permissions(203)]
    assert cache.set_calls[-1] == (
        CacheKeys.app_permissions(203),
        ["device:command:send"],
        CacheExpire.APP_PERMISSIONS,
    )


@pytest.mark.asyncio
async def test_after_permission_change_invalidates_related_app_caches() -> None:
    service = PermissionService(repo=_FakeRepo())  # type: ignore[arg-type]
    invalidated: list[set[int]] = []

    async def fake_query(db: object, permission_id: int) -> set[int]:
        assert permission_id == 11
        return {2, 3}

    async def fake_invalidate(app_ids: set[int]) -> None:
        invalidated.append(set(app_ids))

    service._query_app_ids_by_permission_id = fake_query  # type: ignore[method-assign]
    service._invalidate_app_permissions_for_apps = fake_invalidate  # type: ignore[method-assign]

    context = HookContext(
        session=object(),  # type: ignore[arg-type]
        params={"instance": SimpleNamespace(id=11)},
        results={"affected_app_ids_before": {1, 2}},
    )

    await service._after_permission_change_invalidate_app_permissions(context)

    assert invalidated == [{1, 2, 3}]


@pytest.mark.asyncio
async def test_permission_restore_invalidates_related_user_and_app_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    service = PermissionService(repo=_FakeRepo())  # type: ignore[arg-type]
    invalidated_users: list[set[int]] = []
    invalidated_apps: list[set[int]] = []

    async def fake_restore(self: BaseService, db: object, id: int, cache: object | None = None):
        return SimpleNamespace(id=id)

    async def fake_query_users(db: object, permission_id: int) -> set[int]:
        assert permission_id == 21
        return {2, 7}

    async def fake_query_apps(db: object, permission_id: int) -> set[int]:
        assert permission_id == 21
        return {5, 8}

    async def fake_invalidate_users(user_ids: set[int]) -> None:
        invalidated_users.append(set(user_ids))

    async def fake_invalidate_apps(app_ids: set[int]) -> None:
        invalidated_apps.append(set(app_ids))

    monkeypatch.setattr(BaseService, "restore", fake_restore)
    service._query_user_ids_by_permission_id = fake_query_users  # type: ignore[method-assign]
    service._query_app_ids_by_permission_id = fake_query_apps  # type: ignore[method-assign]
    service._invalidate_permissions_for_users = fake_invalidate_users  # type: ignore[method-assign]
    service._invalidate_app_permissions_for_apps = fake_invalidate_apps  # type: ignore[method-assign]

    restored = await service.restore(object(), 21)

    assert restored is not None
    assert invalidated_users == [{2, 7}]
    assert invalidated_apps == [{5, 8}]


@pytest.mark.asyncio
async def test_permission_permanent_delete_invalidates_related_user_and_app_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PermissionService(repo=_FakeRepo())  # type: ignore[arg-type]
    invalidated_users: list[set[int]] = []
    invalidated_apps: list[set[int]] = []

    async def fake_permanent_delete(self: BaseService, db: object, id: int, cache: object | None = None) -> bool:
        return True

    async def fake_query_users(db: object, permission_id: int) -> set[int]:
        assert permission_id == 34
        return {4, 6}

    async def fake_query_apps(db: object, permission_id: int) -> set[int]:
        assert permission_id == 34
        return {3, 9}

    async def fake_invalidate_users(user_ids: set[int]) -> None:
        invalidated_users.append(set(user_ids))

    async def fake_invalidate_apps(app_ids: set[int]) -> None:
        invalidated_apps.append(set(app_ids))

    monkeypatch.setattr(BaseService, "permanent_delete", fake_permanent_delete)
    service._query_user_ids_by_permission_id = fake_query_users  # type: ignore[method-assign]
    service._query_app_ids_by_permission_id = fake_query_apps  # type: ignore[method-assign]
    service._invalidate_permissions_for_users = fake_invalidate_users  # type: ignore[method-assign]
    service._invalidate_app_permissions_for_apps = fake_invalidate_apps  # type: ignore[method-assign]

    success = await service.permanent_delete(object(), 34)

    assert success is True
    assert invalidated_users == [{4, 6}]
    assert invalidated_apps == [{3, 9}]


@pytest.mark.asyncio
async def test_permission_update_invalidates_related_user_and_app_caches_after_super(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PermissionService(repo=_FakeRepo())  # type: ignore[arg-type]
    call_order: list[str] = []
    user_queries = [{2, 4}, {4, 7}]
    app_queries = [{5}, {5, 9}]
    invalidated_users: list[set[int]] = []
    invalidated_apps: list[set[int]] = []

    async def fake_update(
        self: TreeServiceMixin, db: object, id: int, data: dict[str, object], cache: object | None = None
    ):
        call_order.append("super")
        return SimpleNamespace(id=id)

    async def fake_query_users(db: object, permission_id: int) -> set[int]:
        assert permission_id == 55
        return user_queries.pop(0)

    async def fake_query_apps(db: object, permission_id: int) -> set[int]:
        assert permission_id == 55
        return app_queries.pop(0)

    async def fake_invalidate_users(user_ids: set[int]) -> None:
        call_order.append("invalidate_users")
        invalidated_users.append(set(user_ids))

    async def fake_invalidate_apps(app_ids: set[int]) -> None:
        call_order.append("invalidate_apps")
        invalidated_apps.append(set(app_ids))

    monkeypatch.setattr(TreeServiceMixin, "update", fake_update)
    service._query_user_ids_by_permission_id = fake_query_users  # type: ignore[method-assign]
    service._query_app_ids_by_permission_id = fake_query_apps  # type: ignore[method-assign]
    service._invalidate_permissions_for_users = fake_invalidate_users  # type: ignore[method-assign]
    service._invalidate_app_permissions_for_apps = fake_invalidate_apps  # type: ignore[method-assign]

    updated = await service.update(object(), 55, {"version": 1})

    assert updated is not None
    assert invalidated_users == [{2, 4, 7}]
    assert invalidated_apps == [{5, 9}]
    assert call_order == ["super", "invalidate_users", "invalidate_apps"]


@pytest.mark.asyncio
async def test_permission_soft_delete_invalidates_related_user_and_app_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PermissionService(repo=_FakeRepo())  # type: ignore[arg-type]
    invalidated_users: list[set[int]] = []
    invalidated_apps: list[set[int]] = []

    async def fake_soft_delete(self: TreeServiceMixin, db: object, id: int, cache: object | None = None):
        return SimpleNamespace(id=id)

    async def fake_query_users(db: object, permission_id: int) -> set[int]:
        assert permission_id == 56
        return {1, 8}

    async def fake_query_apps(db: object, permission_id: int) -> set[int]:
        assert permission_id == 56
        return {3, 6}

    async def fake_invalidate_users(user_ids: set[int]) -> None:
        invalidated_users.append(set(user_ids))

    async def fake_invalidate_apps(app_ids: set[int]) -> None:
        invalidated_apps.append(set(app_ids))

    monkeypatch.setattr(TreeServiceMixin, "soft_delete", fake_soft_delete)
    service._query_user_ids_by_permission_id = fake_query_users  # type: ignore[method-assign]
    service._query_app_ids_by_permission_id = fake_query_apps  # type: ignore[method-assign]
    service._invalidate_permissions_for_users = fake_invalidate_users  # type: ignore[method-assign]
    service._invalidate_app_permissions_for_apps = fake_invalidate_apps  # type: ignore[method-assign]

    deleted = await service.soft_delete(object(), 56)

    assert deleted is not None
    assert invalidated_users == [{1, 8}]
    assert invalidated_apps == [{3, 6}]
