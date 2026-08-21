from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_bootstrap_foundation_validates_existing_admin_environment_contract() -> None:
    from scripts.data.bootstrap_foundation import load_bootstrap_foundation_config

    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_USERNAME"):
        load_bootstrap_foundation_config({"BOOTSTRAP_ADMIN_PASSWORD": "StrongPassw0rd!"})
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        load_bootstrap_foundation_config({"BOOTSTRAP_ADMIN_USERNAME": "admin"})
    with pytest.raises(ValueError, match="长度必须至少为 8"):
        load_bootstrap_foundation_config({"BOOTSTRAP_ADMIN_USERNAME": "admin", "BOOTSTRAP_ADMIN_PASSWORD": "short"})

    config = load_bootstrap_foundation_config(
        {
            "BOOTSTRAP_ADMIN_USERNAME": "  prod-admin  ",
            "BOOTSTRAP_ADMIN_PASSWORD": "StrongPassw0rd!",
            "BOOTSTRAP_ADMIN_FULL_NAME": "生产管理员",
            "BOOTSTRAP_ADMIN_EMAIL": "PROD-ADMIN@example.com",
        }
    )

    assert config.username == "prod-admin"
    assert config.password == "StrongPassw0rd!"
    assert config.full_name == "生产管理员"
    assert config.email == "PROD-ADMIN@example.com"


@pytest.mark.asyncio
async def test_bootstrap_foundation_commits_once_before_invalidating_exact_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.data import bootstrap_foundation

    events: list[str] = []
    app = object()
    cache = object()
    authorization = object()
    result = SimpleNamespace(
        authorization=authorization,
        admin_action="created",
        admin_username="prod-admin",
        admin_role_added=True,
    )

    async def bootstrap(*_args: object) -> object:
        events.append("bootstrap")
        return result

    async def commit() -> None:
        events.append("commit")

    async def invalidate(*_args: object) -> None:
        events.append("invalidate")

    session = SimpleNamespace(commit=AsyncMock(side_effect=commit), rollback=AsyncMock())
    monkeypatch.setattr(bootstrap_foundation, "create_app", lambda: app)
    monkeypatch.setattr(bootstrap_foundation, "init_db", AsyncMock())
    monkeypatch.setattr(bootstrap_foundation, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(bootstrap_foundation, "get_cache", lambda: cache)
    monkeypatch.setattr(
        bootstrap_foundation.authorization_bootstrap_service, "bootstrap", AsyncMock(side_effect=bootstrap)
    )
    monkeypatch.setattr(
        bootstrap_foundation.authorization_bootstrap_service,
        "invalidate_caches",
        AsyncMock(side_effect=invalidate),
    )

    config = bootstrap_foundation.BootstrapFoundationConfig("prod-admin", "StrongPassw0rd!")
    exit_code = await bootstrap_foundation.main_async(config)

    assert exit_code == 0
    assert events == ["bootstrap", "commit", "invalidate"]
    bootstrap_foundation.authorization_bootstrap_service.bootstrap.assert_awaited_once_with(app, session, config)
    bootstrap_foundation.authorization_bootstrap_service.invalidate_caches.assert_awaited_once_with(
        authorization,
        cache,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_foundation_rolls_back_precommit_failure_without_invalidating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.data import bootstrap_foundation

    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(bootstrap_foundation, "create_app", lambda: object())
    monkeypatch.setattr(bootstrap_foundation, "init_db", AsyncMock())
    monkeypatch.setattr(bootstrap_foundation, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(
        bootstrap_foundation.authorization_bootstrap_service,
        "bootstrap",
        AsyncMock(side_effect=RuntimeError("precommit failed")),
    )
    monkeypatch.setattr(bootstrap_foundation.authorization_bootstrap_service, "invalidate_caches", AsyncMock())

    with pytest.raises(RuntimeError, match="precommit failed"):
        await bootstrap_foundation.main_async(
            bootstrap_foundation.BootstrapFoundationConfig("prod-admin", "StrongPassw0rd!")
        )

    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    bootstrap_foundation.authorization_bootstrap_service.invalidate_caches.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_foundation_reports_postcommit_cache_failure_without_false_rollback_or_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.data import bootstrap_foundation

    result = SimpleNamespace(
        authorization=object(),
        admin_action="created",
        admin_username="prod-admin",
        admin_role_added=True,
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(bootstrap_foundation, "create_app", lambda: object())
    monkeypatch.setattr(bootstrap_foundation, "init_db", AsyncMock())
    monkeypatch.setattr(bootstrap_foundation, "get_db_context", lambda: _SessionContext(session))
    monkeypatch.setattr(bootstrap_foundation, "get_cache", lambda: object())
    monkeypatch.setattr(
        bootstrap_foundation.authorization_bootstrap_service,
        "bootstrap",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        bootstrap_foundation.authorization_bootstrap_service,
        "invalidate_caches",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )

    exit_code = await bootstrap_foundation.main_async(
        bootstrap_foundation.BootstrapFoundationConfig("prod-admin", "StrongPassw0rd!")
    )

    captured = capsys.readouterr()
    assert exit_code == bootstrap_foundation.POSTCOMMIT_CACHE_FAILURE_EXIT_CODE
    assert captured.err.splitlines() == [
        "DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED",
        "CACHE_INVALIDATION_FAILURE_DETAIL: RuntimeError: redis unavailable",
    ]
    assert "回滚" not in captured.out + captured.err
    assert "✅" not in captured.out
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


def test_bootstrap_foundation_shell_wrapper_only_forwards_to_python(tmp_path: Path) -> None:
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
        ["/bin/bash", str(BACKEND_ROOT / "scripts/data/bootstrap_foundation.sh"), "--unexpected"],
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "UV_CALL_LOG": str(call_log)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        f"cwd={BACKEND_ROOT}",
        "args=run python scripts/data/bootstrap_foundation.py --unexpected",
    ]


def test_bootstrap_foundation_shell_wrapper_uses_runtime_python_without_uv(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "python-call.log"
    python = bin_dir / "python"
    python.write_text(
        '#!/bin/sh\nprintf "cwd=%s\\n" "$PWD" >"$PYTHON_CALL_LOG"\nprintf "args=%s\\n" "$*" >>"$PYTHON_CALL_LOG"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)

    completed = subprocess.run(
        ["/bin/bash", str(BACKEND_ROOT / "scripts/data/bootstrap_foundation.sh"), "--unexpected"],
        cwd=tmp_path,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PYTHON_CALL_LOG": str(call_log),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        f"cwd={BACKEND_ROOT}",
        "args=scripts/data/bootstrap_foundation.py --unexpected",
    ]
