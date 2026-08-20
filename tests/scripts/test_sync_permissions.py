from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.data import sync_permissions
from src.app.admin.services import PermissionCatalogSyncResult


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_permission_cli_materializes_catalog_and_preserves_commit_and_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = object()
    session = SimpleNamespace(commit=AsyncMock())
    catalog_sync = AsyncMock(
        return_value=PermissionCatalogSyncResult(created=3, updated=2, deleted=1, unchanged=4, total=10)
    )
    monkeypatch.setattr(sync_permissions, "create_app", lambda: app)
    monkeypatch.setattr(sync_permissions, "scan_routes_for_permissions", lambda _app: [{"name": "one"}])
    monkeypatch.setattr(sync_permissions, "init_db", AsyncMock())
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(sync_permissions.permission_catalog_service, "sync", catalog_sync)

    await sync_permissions.main_async(Namespace(preview=False, dry_run=False, permissions_only=True))

    catalog_sync.assert_awaited_once_with(app, session, dry_run=False)
    session.commit.assert_awaited_once_with()
    output = capsys.readouterr().out
    assert "新增 3 条，更新 2 条，跳过 4 条，扫描总数 1 条" in output
