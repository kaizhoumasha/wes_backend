import pytest

from src.core import rbac
from src.core.rbac import get_user_permissions, invalidate_users_permissions


class _FakeCache:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []
        self.storage: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, int | None]] = []

    async def get(self, key: str) -> object | None:
        return self.storage.get(key)

    async def set(self, key: str, value: object, expire: int | None = None) -> None:
        self.storage[key] = value
        self.set_calls.append((key, value, expire))

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)
        self.storage.pop(key, None)


@pytest.mark.asyncio()
async def test_invalidate_users_permissions_deduplicates_user_ids() -> None:
    cache = _FakeCache()

    await invalidate_users_permissions(cache, [101, 101, 202, -1, "x"])  # type: ignore[list-item]

    assert sorted(cache.deleted_keys) == ["perms:user:101", "perms:user:202"]


@pytest.mark.asyncio()
async def test_invalidate_users_permissions_handles_empty_input() -> None:
    cache = _FakeCache()

    await invalidate_users_permissions(cache, [])

    assert cache.deleted_keys == []


@pytest.mark.asyncio()
async def test_get_user_permissions_caches_empty_permission_set(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _FakeCache()

    class _FakePermissionService:
        async def get_user_permissions(self, db: object, user_id: int) -> set[str]:
            assert user_id == 303
            return set()

    monkeypatch.setattr("src.app.admin.services.permission_service", _FakePermissionService())

    permissions = await get_user_permissions(object(), 303, cache)

    assert permissions == set()
    assert cache.set_calls == [("perms:user:303", [], rbac.PERM_EMPTY_CACHE_TTL)]


@pytest.mark.asyncio()
async def test_get_user_permissions_deletes_invalid_cached_value(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _FakeCache()
    cache.storage["perms:user:404"] = "{bad-json"

    class _FakePermissionService:
        async def get_user_permissions(self, db: object, user_id: int) -> set[str]:
            assert user_id == 404
            return {"menu:view"}

    monkeypatch.setattr("src.app.admin.services.permission_service", _FakePermissionService())

    permissions = await get_user_permissions(object(), 404, cache)

    assert permissions == {"menu:view"}
    assert cache.deleted_keys == ["perms:user:404"]
    assert cache.set_calls[-1] == ("perms:user:404", ["menu:view"], rbac.PERM_CACHE_TTL)


@pytest.mark.asyncio()
async def test_get_user_permissions_reads_cached_permission_list_without_crashing() -> None:
    cache = _FakeCache()
    cache.storage["perms:user:505"] = ["*"]

    permissions = await get_user_permissions(object(), 505, cache)

    assert permissions == {"*"}
    assert cache.deleted_keys == []
    assert cache.set_calls == []


@pytest.mark.asyncio()
async def test_get_user_permissions_rejects_mapping_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _FakeCache()
    cache.storage["perms:user:606"] = {"*": True}

    class _FakePermissionService:
        async def get_user_permissions(self, db: object, user_id: int) -> set[str]:
            assert user_id == 606
            return {"menu:view"}

    monkeypatch.setattr("src.app.admin.services.permission_service", _FakePermissionService())

    permissions = await get_user_permissions(object(), 606, cache)

    assert permissions == {"menu:view"}
    assert cache.deleted_keys == ["perms:user:606"]
    assert cache.set_calls[-1] == ("perms:user:606", ["menu:view"], rbac.PERM_CACHE_TTL)
