from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI

from src.app.api_auth.v1 import api_application as api_application_module
from src.core.base_api import BaseAPI
from src.utils.permission_scanner import scan_routes_for_permissions, sync_builtin_role_permissions


class DummySoftDeleteModel:
    is_deleted = True

    def soft_delete(self, deleted_by: int | None = None) -> None:
        return None

    def restore(self) -> None:
        return None


class _FakeService:
    pass


class _ScalarResult:
    def __init__(self, items: list[object]):
        self._items = items

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._items))


class _RowsResult:
    def __init__(self, rows: list[tuple[int, int]]):
        self._rows = rows

    def all(self) -> list[tuple[int, int]]:
        return list(self._rows)


def test_scan_routes_for_permissions_includes_permanent_delete_action() -> None:
    app = FastAPI()
    api = BaseAPI(
        module_name="test",
        model=DummySoftDeleteModel,
        service=_FakeService(),
        response_schema=dict,
        prefix="/dummy-items",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=True,
    )
    app.include_router(api.router)

    scanned = scan_routes_for_permissions(app)
    by_name = {item["name"]: item for item in scanned}

    assert "test:dummysoftdeletemodel:permanent_delete" in by_name
    assert by_name["test:dummysoftdeletemodel:permanent_delete"]["action"] == "permanent_delete"
    assert by_name["test:dummysoftdeletemodel:permanent_delete"]["path"] == "/dummy-items/trash/permanent"


def test_scan_routes_for_permissions_uses_api_application_permission_resource_override() -> None:
    app = FastAPI()
    app.include_router(api_application_module.router, prefix="/api")

    scanned_names = {item["name"] for item in scan_routes_for_permissions(app)}

    assert "api-auth:api_application:list" in scanned_names
    assert "api-auth:api_application:detail" in scanned_names
    assert "api-auth:api_application:permanent_delete" in scanned_names
    assert not any(":apiapplication:" in name for name in scanned_names)


async def test_sync_builtin_role_permissions_does_not_grant_permanent_delete_to_read_only_roles() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ScalarResult(
                [
                    SimpleNamespace(id=1, name="运营人员"),
                    SimpleNamespace(id=2, name="普通用户"),
                ]
            ),
            _ScalarResult(
                [
                    SimpleNamespace(id=11, name="admin:menu:list"),
                    SimpleNamespace(id=12, name="admin:menu:detail"),
                    SimpleNamespace(id=13, name="admin:menu:permanent_delete"),
                ]
            ),
            _RowsResult([]),
        ]
    )

    result = await sync_builtin_role_permissions(db, dry_run=True, auto_commit=False)

    assert result == {"added": 4, "skipped": 0, "roles_processed": 2}
    assert db.execute.await_count == 3
    db.commit.assert_not_awaited()
