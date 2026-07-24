from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.data.provision_e2e_callback_application import (
    CALLBACK_PERMISSION,
    provision_e2e_callback_application,
)
from src.core.encryption import encryption_service


@pytest.mark.asyncio
async def test_e2e_callback_provisioning_creates_app_and_assigns_only_callback_permission() -> None:
    application = SimpleNamespace(id=17, version=1)
    app_service = SimpleNamespace(
        get_by_app_id=AsyncMock(return_value=None),
        create=AsyncMock(return_value=application),
        update=AsyncMock(),
        assign_permissions=AsyncMock(),
    )
    permission_service = SimpleNamespace(
        get_api_permissions=AsyncMock(
            return_value=[
                SimpleNamespace(id=23, name=CALLBACK_PERMISSION),
                SimpleNamespace(id=24, name="api:other:event"),
            ]
        )
    )

    db = object()
    cache = object()
    application_id = await provision_e2e_callback_application(
        db,
        cache,
        app_id="app_local_mock",
        app_secret="local_mock_change_me",
        app_service=app_service,
        permissions=permission_service,
    )

    assert application_id == 17
    create_data = app_service.create.await_args.args[1]
    assert create_data["app_id"] == "app_local_mock"
    assert encryption_service.decrypt(create_data["app_secret_encrypted"]) == "local_mock_change_me"
    app_service.assign_permissions.assert_awaited_once_with(db, cache, 17, [23])


@pytest.mark.asyncio
async def test_e2e_callback_provisioning_refreshes_existing_app_without_duplicate_create() -> None:
    existing = SimpleNamespace(id=17, version=4)
    refreshed = SimpleNamespace(id=17, version=5)
    app_service = SimpleNamespace(
        get_by_app_id=AsyncMock(return_value=existing),
        create=AsyncMock(),
        update=AsyncMock(return_value=refreshed),
        assign_permissions=AsyncMock(),
    )
    permission_service = SimpleNamespace(
        get_api_permissions=AsyncMock(return_value=[SimpleNamespace(id=23, name=CALLBACK_PERMISSION)])
    )

    await provision_e2e_callback_application(
        object(),
        object(),
        app_id="app_local_mock",
        app_secret="rotated-test-secret",
        app_service=app_service,
        permissions=permission_service,
    )

    app_service.create.assert_not_awaited()
    assert app_service.update.await_args.args[1] == 17
    assert app_service.update.await_args.args[2]["version"] == 4
    assert encryption_service.decrypt(app_service.update.await_args.args[2]["app_secret_encrypted"]) == (
        "rotated-test-secret"
    )


def test_e2e_startup_provisions_callback_application_before_starting_services() -> None:
    script = (Path(__file__).resolve().parents[2] / "scripts/start_e2e_env.sh").read_text()

    build = "build api mock_ecs mock_wms"
    provision = "python scripts/data/provision_e2e_callback_application.py"
    assert build in script
    assert provision in script
    assert (
        script.index(build)
        < script.index("alembic upgrade head")
        < script.index(provision)
        < script.index("--profile e2e up -d")
    )
