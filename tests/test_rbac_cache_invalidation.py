import pytest

from src.core.rbac import invalidate_users_permissions


class _FakeCache:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)


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
