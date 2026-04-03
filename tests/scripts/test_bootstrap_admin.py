from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_first_superuser() -> None:
    from scripts.data.bootstrap_admin import BootstrapAdminConfig, bootstrap_admin

    repo = SimpleNamespace(
        get_first_superuser=AsyncMock(return_value=None),
        create=AsyncMock(return_value=SimpleNamespace(id=1, username="prod-admin")),
    )

    config = BootstrapAdminConfig(
        username="prod-admin",
        password="StrongPassw0rd!",
        full_name="生产管理员",
        email="prod-admin@example.com",
    )

    result = await bootstrap_admin(db=object(), config=config, repo=repo)

    assert result.action == "created"
    assert result.username == "prod-admin"
    repo.get_first_superuser.assert_awaited_once()
    repo.create.assert_awaited_once()
    payload = repo.create.await_args.args[1]
    assert payload["username"] == "prod-admin"
    assert payload["email"] == "prod-admin@example.com"
    assert payload["full_name"] == "生产管理员"
    assert payload["is_superuser"] is True
    assert payload["is_multi_login"] is True
    assert payload["hashed_password"] != "StrongPassw0rd!"


@pytest.mark.asyncio
async def test_bootstrap_admin_skips_when_superuser_already_exists() -> None:
    from scripts.data.bootstrap_admin import BootstrapAdminConfig, bootstrap_admin

    repo = SimpleNamespace(
        get_first_superuser=AsyncMock(return_value=SimpleNamespace(id=99, username="existing-admin")),
        create=AsyncMock(),
    )

    config = BootstrapAdminConfig(
        username="prod-admin",
        password="StrongPassw0rd!",
        full_name="生产管理员",
        email="prod-admin@example.com",
    )

    result = await bootstrap_admin(db=object(), config=config, repo=repo)

    assert result.action == "skipped"
    assert result.username == "existing-admin"
    repo.get_first_superuser.assert_awaited_once()
    repo.create.assert_not_called()
