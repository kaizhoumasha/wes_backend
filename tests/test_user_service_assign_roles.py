import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.admin.repositories.role_repository import RoleRepository
from src.app.admin.repositories.user_repository import UserRepository
from src.app.admin.services.role_service import RoleService
from src.app.admin.services.user_service import UserService
from src.core.base_service import BaseService
from src.core.exceptions import NotFoundException


@pytest.mark.asyncio
async def test_role_service_get_active_roles_by_ids_rejects_deleted_role() -> None:
    service = RoleService(RoleRepository())
    deleted_role = SimpleNamespace(id=2, is_deleted=True)
    service.repo.get_by_ids = AsyncMock(return_value=[deleted_role])  # type: ignore[method-assign]

    with pytest.raises(NotFoundException, match="角色 2 已删除"):
        await service.get_active_roles_by_ids(object(), [2])


@pytest.mark.asyncio
async def test_role_service_restore_invalidates_related_user_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    service = RoleService(RoleRepository())
    service._query_user_ids_by_role_id = AsyncMock(return_value={3, 8})  # type: ignore[method-assign]
    service._invalidate_permissions_for_users = AsyncMock()  # type: ignore[method-assign]
    db = object()

    async def fake_restore(self: BaseService, db: object, id: int, cache: object | None = None):
        return SimpleNamespace(id=id)

    monkeypatch.setattr(BaseService, "restore", fake_restore)

    restored = await service.restore(db, 12)

    assert restored is not None
    service._query_user_ids_by_role_id.assert_awaited_once_with(db, 12)  # type: ignore[attr-defined]
    service._invalidate_permissions_for_users.assert_awaited_once_with({3, 8})  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_role_service_permanent_delete_invalidates_related_user_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RoleService(RoleRepository())
    service._query_user_ids_by_role_id = AsyncMock(return_value={4, 9})  # type: ignore[method-assign]
    service._invalidate_permissions_for_users = AsyncMock()  # type: ignore[method-assign]
    db = object()

    async def fake_permanent_delete(self: BaseService, db: object, id: int, cache: object | None = None) -> bool:
        return True

    monkeypatch.setattr(BaseService, "permanent_delete", fake_permanent_delete)

    success = await service.permanent_delete(db, 18)

    assert success is True
    service._query_user_ids_by_role_id.assert_awaited_once_with(db, 18)  # type: ignore[attr-defined]
    service._invalidate_permissions_for_users.assert_awaited_once_with({4, 9})  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_service_restore_invalidates_own_permission_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    service = UserService(UserRepository())
    service._invalidate_permissions_for_user = AsyncMock()  # type: ignore[method-assign]

    async def fake_restore(self: BaseService, db: object, id: int, cache: object | None = None):
        return SimpleNamespace(id=id)

    monkeypatch.setattr(BaseService, "restore", fake_restore)

    restored = await service.restore(object(), 21)

    assert restored is not None
    service._invalidate_permissions_for_user.assert_awaited_once_with(21)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_service_permanent_delete_invalidates_own_permission_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    service = UserService(UserRepository())
    service._invalidate_permissions_for_user = AsyncMock()  # type: ignore[method-assign]

    async def fake_permanent_delete(self: BaseService, db: object, id: int, cache: object | None = None) -> bool:
        return True

    monkeypatch.setattr(BaseService, "permanent_delete", fake_permanent_delete)

    success = await service.permanent_delete(object(), 22)

    assert success is True
    service._invalidate_permissions_for_user.assert_awaited_once_with(22)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_service_assign_roles_uses_role_service(monkeypatch: pytest.MonkeyPatch) -> None:
    user_service_module = importlib.import_module("src.app.admin.services.user_service")
    service = UserService(UserRepository())
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    cache = object()
    user = SimpleNamespace(id=7, roles=[])
    roles = [SimpleNamespace(id=11, is_deleted=False), SimpleNamespace(id=12, is_deleted=False)]

    service.repo.get_by_id_with_roles = AsyncMock(return_value=user)  # type: ignore[method-assign]
    service.repo.get_by_id = AsyncMock(side_effect=AssertionError("assign_roles 应预加载 roles"))  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock()
    service._invalidate_permissions_for_user = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        user_service_module, "set_attribute", lambda instance, key, value: setattr(instance, key, value)
    )

    role_service_mock = SimpleNamespace(get_active_roles_by_ids=AsyncMock(return_value=roles))
    role_service_module = importlib.import_module("src.app.admin.services.role_service")
    monkeypatch.setattr(role_service_module, "role_service", role_service_mock)

    result = await service.assign_roles(db=db, user_id=7, role_ids=[11, 12], cache=cache)

    assert result is user
    assert user.roles == roles
    role_service_mock.get_active_roles_by_ids.assert_awaited_once_with(db, [11, 12])
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    assert db.refresh.await_count == 0
    assert service.repo.get_by_id_with_roles.await_count == 1  # type: ignore[attr-defined]
    service.invalidate_cache.assert_awaited_once_with(cache, 7, invalidate_list=True)
    service._invalidate_permissions_for_user.assert_awaited_once_with(7)
