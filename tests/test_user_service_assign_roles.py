import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.admin.repositories.role_repository import RoleRepository
from src.app.admin.repositories.user_repository import UserRepository
from src.app.admin.services.role_service import RoleService
from src.app.admin.services.user_service import UserService
from src.core.exceptions import NotFoundException


@pytest.mark.asyncio
async def test_role_service_get_active_roles_by_ids_rejects_deleted_role() -> None:
    service = RoleService(RoleRepository())
    deleted_role = SimpleNamespace(id=2, is_deleted=True)
    service.repo.get_by_ids = AsyncMock(return_value=[deleted_role])  # type: ignore[method-assign]

    with pytest.raises(NotFoundException, match="角色 2 已删除"):
        await service.get_active_roles_by_ids(object(), [2])


@pytest.mark.asyncio
async def test_user_service_assign_roles_uses_role_service(monkeypatch: pytest.MonkeyPatch) -> None:
    user_service_module = importlib.import_module("src.app.admin.services.user_service")
    service = UserService(UserRepository())
    db = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    cache = object()
    user = SimpleNamespace(id=7, roles=[])
    roles = [SimpleNamespace(id=11, is_deleted=False), SimpleNamespace(id=12, is_deleted=False)]

    service.repo.get_by_id_with_roles = AsyncMock(return_value=user)  # type: ignore[method-assign]
    service.repo.get_by_id = AsyncMock(side_effect=AssertionError("assign_roles 应预加载 roles"))  # type: ignore[method-assign]
    service.invalidate_cache = AsyncMock()
    service._invalidate_permissions_for_user = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(user_service_module, "set_attribute", lambda instance, key, value: setattr(instance, key, value))

    role_service_mock = SimpleNamespace(get_active_roles_by_ids=AsyncMock(return_value=roles))
    role_service_module = importlib.import_module("src.app.admin.services.role_service")
    monkeypatch.setattr(role_service_module, "role_service", role_service_mock)

    result = await service.assign_roles(db=db, user_id=7, role_ids=[11, 12], cache=cache)

    assert result is user
    assert user.roles == roles
    role_service_mock.get_active_roles_by_ids.assert_awaited_once_with(db, [11, 12])
    db.flush.assert_awaited_once()
    assert db.refresh.await_count == 0
    assert service.repo.get_by_id_with_roles.await_count == 1  # type: ignore[attr-defined]
    service.invalidate_cache.assert_awaited_once_with(cache, 7)
    service._invalidate_permissions_for_user.assert_awaited_once_with(7)
