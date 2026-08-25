from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.admin.models import UserResponse
from src.app.auth.models import AuthMyResponse
from src.app.auth.v1 import auth as auth_module
from src.register import create_app
from src.utils.timezone import timezone


def test_auth_my_response_contains_only_user_and_permissions() -> None:
    assert set(AuthMyResponse.model_fields) == {"user", "permissions"}


def test_admin_menu_routes_are_absent_from_openapi() -> None:
    assert not any(path.startswith("/api/v1/admin/menus") for path in create_app().openapi()["paths"])


@pytest.mark.asyncio
async def test_auth_my_handler_uses_only_user_and_permission_services(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not hasattr(auth_module, "menu_service")

    user = UserResponse(
        id=1,
        username="operator",
        email="operator@example.com",
        is_superuser=False,
        is_multi_login=False,
        created_at=timezone.now_utc(),
    )
    permission = SimpleNamespace(
        id=2,
        name="admin:user:list",
        description="查看用户",
        type="user_api",
        category="admin",
        resource="user",
        action="list",
        method="GET",
        path="/api/v1/admin/users",
    )
    get_user_profile = AsyncMock(return_value=user)
    get_user_api_permissions = AsyncMock(return_value=[permission])
    monkeypatch.setattr(auth_module.auth_service, "get_user_profile", get_user_profile)
    monkeypatch.setattr(auth_module.permission_service, "get_user_api_permissions", get_user_api_permissions)

    response = await auth_module.get_my_context(db=object(), current_user=1)  # type: ignore[arg-type]

    assert set(response["data"].model_dump()) == {"user", "permissions"}
    get_user_profile.assert_awaited_once()
    get_user_api_permissions.assert_awaited_once()
