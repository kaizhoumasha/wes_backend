from __future__ import annotations

import os
import subprocess
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.data import sync_permissions
from src.app.admin.services import AuthorizationSyncResult, PermissionCatalogSyncResult

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


def _result(
    *,
    roles_created: int = 0,
    permissions_created: int = 0,
    role_permissions_added: int = 0,
    user_ids: frozenset[int] = frozenset(),
    app_ids: frozenset[int] = frozenset(),
) -> AuthorizationSyncResult:
    return AuthorizationSyncResult(
        roles={"created": roles_created, "updated": 0, "skipped": 5 - roles_created},
        permissions=PermissionCatalogSyncResult(
            created=permissions_created,
            updated=0,
            deleted=0,
            unchanged=10 - permissions_created,
            total=10,
            affected_user_ids=user_ids,
            affected_app_ids=app_ids,
        ),
        role_permissions={
            "added": role_permissions_added,
            "removed": 0,
            "skipped": 10 - role_permissions_added,
            "roles_processed": 5,
        },
        affected_user_ids=user_ids,
    )


def test_permission_cli_requires_exactly_one_mode() -> None:
    parser = sync_permissions.build_parser()

    for args in ([], ["--check", "--apply"], ["--preview", "--repair-cache"]):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(args)
        assert exc_info.value.code == 2

    for mode in ("--check", "--apply", "--preview", "--repair-cache"):
        assert vars(parser.parse_args([mode]))[mode.removeprefix("--").replace("-", "_")] is True


@pytest.mark.parametrize(
    "result",
    [
        _result(roles_created=1),
        _result(permissions_created=1),
        _result(role_permissions_added=1),
    ],
)
@pytest.mark.asyncio
async def test_permission_check_uses_database_dry_run_and_fails_on_any_delta(
    monkeypatch: pytest.MonkeyPatch,
    result: AuthorizationSyncResult,
) -> None:
    app = object()
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    converge = AsyncMock(return_value=result)
    monkeypatch.setattr(sync_permissions, "create_app", lambda: app)
    monkeypatch.setattr(sync_permissions, "init_db", AsyncMock())
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(sync_permissions.authorization_bootstrap_service, "converge_authorization", converge)

    exit_code = await sync_permissions.main_async(Namespace(check=True, apply=False, preview=False, repair_cache=False))

    assert exit_code != 0
    converge.assert_awaited_once_with(app, session, dry_run=True)
    session.commit.assert_not_awaited()
    sync_permissions.init_db.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_permission_check_succeeds_only_when_all_authorization_domains_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(sync_permissions, "create_app", lambda: object())
    monkeypatch.setattr(sync_permissions, "init_db", AsyncMock())
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "converge_authorization",
        AsyncMock(return_value=_result()),
    )

    exit_code = await sync_permissions.main_async(Namespace(check=True, apply=False, preview=False, repair_cache=False))

    assert exit_code == 0
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_apply_commits_once_then_invalidates_exact_service_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    app = object()
    cache = object()
    result = _result(permissions_created=1, user_ids=frozenset({11}), app_ids=frozenset({22}))

    async def converge(*_args: object, **_kwargs: object) -> AuthorizationSyncResult:
        events.append("converge")
        return result

    async def commit() -> None:
        events.append("commit")

    async def invalidate(*_args: object) -> None:
        events.append("invalidate")

    session = SimpleNamespace(commit=AsyncMock(side_effect=commit), rollback=AsyncMock())
    monkeypatch.setattr(sync_permissions, "create_app", lambda: app)
    monkeypatch.setattr(sync_permissions, "init_db", AsyncMock())
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(sync_permissions, "get_cache", lambda: cache)
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "converge_authorization",
        AsyncMock(side_effect=converge),
    )
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "invalidate_caches",
        AsyncMock(side_effect=invalidate),
    )

    exit_code = await sync_permissions.main_async(Namespace(check=False, apply=True, preview=False, repair_cache=False))

    assert exit_code == 0
    assert events == ["converge", "commit", "invalidate"]
    sync_permissions.authorization_bootstrap_service.converge_authorization.assert_awaited_once_with(
        app,
        session,
        dry_run=False,
    )
    sync_permissions.authorization_bootstrap_service.invalidate_caches.assert_awaited_once_with(result, cache)
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_apply_rolls_back_precommit_failure_without_invalidating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(sync_permissions, "create_app", lambda: object())
    monkeypatch.setattr(sync_permissions, "init_db", AsyncMock())
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "converge_authorization",
        AsyncMock(side_effect=RuntimeError("precommit failed")),
    )
    monkeypatch.setattr(sync_permissions.authorization_bootstrap_service, "invalidate_caches", AsyncMock())

    with pytest.raises(RuntimeError, match="precommit failed"):
        await sync_permissions.main_async(Namespace(check=False, apply=True, preview=False, repair_cache=False))

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    sync_permissions.authorization_bootstrap_service.invalidate_caches.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_apply_reports_postcommit_cache_failure_without_false_rollback_or_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(sync_permissions, "create_app", lambda: object())
    monkeypatch.setattr(sync_permissions, "init_db", AsyncMock())
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(sync_permissions, "get_cache", lambda: object())
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "converge_authorization",
        AsyncMock(return_value=_result()),
    )
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "invalidate_caches",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )

    exit_code = await sync_permissions.main_async(Namespace(check=False, apply=True, preview=False, repair_cache=False))

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED" in captured.err
    assert "回滚" not in captured.out + captured.err
    assert "✅" not in captured.out
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_preview_uses_only_pure_catalog_scanning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = object()
    catalog = [
        {
            "name": "admin:user:list",
            "type": "user_api",
            "method": "GET",
            "path": "/api/v1/admin/users",
            "description": "用户列表",
        }
    ]
    monkeypatch.setattr(sync_permissions, "create_app", lambda: app)
    monkeypatch.setattr(
        sync_permissions, "build_permission_catalog", lambda actual_app: catalog if actual_app is app else []
    )
    monkeypatch.setattr(
        sync_permissions, "init_db", AsyncMock(side_effect=AssertionError("must not connect PostgreSQL"))
    )
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: pytest.fail("must not open PostgreSQL context"))
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "converge_authorization",
        AsyncMock(side_effect=AssertionError("must not fabricate database role state")),
    )

    exit_code = await sync_permissions.main_async(Namespace(check=False, apply=False, preview=True, repair_cache=False))

    assert exit_code == 0
    assert "admin:user:list" in capsys.readouterr().out
    sync_permissions.init_db.assert_not_awaited()
    sync_permissions.authorization_bootstrap_service.converge_authorization.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_repair_cache_never_builds_catalog_or_connects_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = object()
    repair = AsyncMock()
    monkeypatch.setattr(sync_permissions, "create_app", lambda: pytest.fail("must not build app"))
    monkeypatch.setattr(sync_permissions, "build_permission_catalog", lambda _app: pytest.fail("must not scan catalog"))
    monkeypatch.setattr(
        sync_permissions, "init_db", AsyncMock(side_effect=AssertionError("must not connect PostgreSQL"))
    )
    monkeypatch.setattr(sync_permissions, "get_db_context", lambda: pytest.fail("must not open PostgreSQL context"))
    monkeypatch.setattr(sync_permissions, "get_cache", lambda: cache)
    monkeypatch.setattr(sync_permissions.authorization_bootstrap_service, "repair_permission_cache_namespaces", repair)

    exit_code = await sync_permissions.main_async(Namespace(check=False, apply=False, preview=False, repair_cache=True))

    assert exit_code == 0
    repair.assert_awaited_once_with(cache)
    sync_permissions.init_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_repair_cache_fails_when_deletion_cannot_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync_permissions, "get_cache", lambda: object())
    monkeypatch.setattr(
        sync_permissions.authorization_bootstrap_service,
        "repair_permission_cache_namespaces",
        AsyncMock(side_effect=RuntimeError("namespace deletion unconfirmed")),
    )

    exit_code = await sync_permissions.main_async(Namespace(check=False, apply=False, preview=False, repair_cache=True))

    assert exit_code != 0


def test_permission_shell_wrapper_forwards_mode_without_business_branching(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "uv-call.log"
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "cwd=%s\\n" "$PWD" >"$UV_CALL_LOG"\nprintf "args=%s\\n" "$*" >>"$UV_CALL_LOG"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(BACKEND_ROOT / "scripts/data/sync_permissions.sh"), "--repair-cache"],
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "UV_CALL_LOG": str(call_log)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        f"cwd={BACKEND_ROOT}",
        "args=run python scripts/data/sync_permissions.py --repair-cache",
    ]
