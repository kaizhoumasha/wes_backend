"""数据库 Engine 配置与进程/事件循环所有权合同。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.pool import StaticPool


class _SessionContext:
    def __init__(self) -> None:
        self.close = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> object:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def db_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    from src.database import db

    engine = MagicMock(sync_engine=MagicMock())
    engine.dispose = AsyncMock()
    create_engine = MagicMock(return_value=engine)
    session_factory = MagicMock(side_effect=_SessionContext)
    sessionmaker = MagicMock(return_value=session_factory)
    monkeypatch.setattr(db, "create_async_engine", create_engine)
    monkeypatch.setattr(db, "async_sessionmaker", sessionmaker)
    monkeypatch.setattr(db, "configure_sqlite_schemas", MagicMock())
    monkeypatch.setattr(db, "get_schema_search_path", MagicMock(return_value="app,public"))
    monkeypatch.setattr(db, "engine", None)
    monkeypatch.setattr(db, "AsyncSessionLocal", None)
    for name in (
        "_engine_generation",
        "_session_factory_generation",
        "_engine_owner_pid",
        "_engine_owner_loop_id",
        "_engine_owner_role",
    ):
        monkeypatch.setattr(db, name, None, raising=False)
    return db


def _settings(database_url: str, *, role: str = "celery", pool_size: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        DATABASE_URL=database_url,
        DATABASE_RUNTIME_ROLE=role,
        DATABASE_POOL_SIZE=pool_size,
        DATABASE_MAX_OVERFLOW=0,
        DATABASE_POOL_TIMEOUT=30,
        DATABASE_APPLICATION_NAME="test",
        DATABASE_APPLICATION_RUN_ID=None,
        APP_ENV="test",
    )


def test_init_db_is_idempotent_for_same_pid_loop_and_role(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))

    async def initialize_twice() -> None:
        await db_module.init_db()
        first_engine = db_module.engine
        await db_module.init_db()
        assert db_module.engine is first_engine

    asyncio.run(initialize_twice())

    db_module.create_async_engine.assert_called_once()


def test_database_generation_ids_publish_stably_rotate_and_clear(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))

    async def lifecycle() -> None:
        await db_module.init_db()
        first = (db_module._engine_generation, db_module._session_factory_generation)
        assert all(isinstance(generation, str) and generation for generation in first)
        assert first[0] != first[1]
        await db_module.init_db()
        assert (db_module._engine_generation, db_module._session_factory_generation) == first
        await db_module.close_db()
        assert db_module._engine_generation is None
        assert db_module._session_factory_generation is None
        await db_module.init_db()
        second = (db_module._engine_generation, db_module._session_factory_generation)
        assert second[0] != first[0]
        assert second[1] != first[1]

    asyncio.run(lifecycle())


def test_init_db_rejects_same_pid_from_foreign_loop(db_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))
    asyncio.run(db_module.init_db())

    with pytest.raises(RuntimeError, match=r"(?i)(owner|event loop|loop)"):
        asyncio.run(db_module.init_db())


def test_init_db_rejects_role_change_in_same_pid_and_loop(db_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings("sqlite+aiosqlite:///:memory:", role="celery")
    monkeypatch.setattr(db_module, "settings", settings)

    async def initialize_then_change_role() -> None:
        await db_module.init_db()
        settings.DATABASE_RUNTIME_ROLE = "api"
        with pytest.raises(RuntimeError, match=r"(?i)(owner|role)"):
            await db_module.init_db()

    asyncio.run(initialize_then_change_role())


def test_forked_process_rejects_inherited_engine_without_disposing_it(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))
    asyncio.run(db_module.init_db())
    inherited_engine = db_module.engine
    monkeypatch.setattr(os, "getpid", lambda: 999_002)

    with pytest.raises(RuntimeError, match=r"(?i)(owner|pid|fork)"):
        asyncio.run(db_module.init_db())

    assert db_module.engine is inherited_engine
    inherited_engine.dispose.assert_not_awaited()


def test_non_owner_cannot_get_session_or_close_engine(db_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))
    asyncio.run(db_module.init_db())
    owned_engine = db_module.engine
    monkeypatch.setattr(os, "getpid", lambda: 999_003)

    async def get_session() -> None:
        async with db_module.get_db_context():
            pass

    with pytest.raises(RuntimeError, match=r"(?i)(owner|pid|fork)"):
        asyncio.run(get_session())
    with pytest.raises(RuntimeError, match=r"(?i)(owner|pid|fork)"):
        asyncio.run(db_module.close_db())

    owned_engine.dispose.assert_not_awaited()


def test_non_owner_get_db_dependency_fails_before_yielding_session(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))
    asyncio.run(db_module.init_db())
    monkeypatch.setattr(os, "getpid", lambda: 999_004)

    async def consume_dependency() -> None:
        generator = db_module.get_db()
        try:
            await anext(generator)
        finally:
            await generator.aclose()

    with pytest.raises(RuntimeError, match=r"(?i)(owner|pid|fork)"):
        asyncio.run(consume_dependency())


def test_owner_close_clears_engine_factory_and_owner_metadata(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))

    async def initialize_and_close() -> MagicMock:
        await db_module.init_db()
        owned_engine = db_module.engine
        assert db_module._engine_owner_pid == os.getpid()
        assert db_module._engine_owner_loop_id == id(asyncio.get_running_loop())
        assert db_module._engine_owner_role == "celery"
        await db_module.close_db()
        return owned_engine

    owned_engine = asyncio.run(initialize_and_close())

    owned_engine.dispose.assert_awaited_once()
    assert db_module.engine is None
    assert db_module.AsyncSessionLocal is None
    assert db_module._engine_owner_pid is None
    assert db_module._engine_owner_loop_id is None
    assert db_module._engine_owner_role is None


def test_owner_close_unpublishes_database_before_bounded_dispose(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))
    observed_during_dispose: list[tuple[Any, ...]] = []

    async def observe_unpublished_state() -> None:
        observed_during_dispose.append(
            (
                db_module.engine,
                db_module.AsyncSessionLocal,
                db_module._engine_owner_pid,
                db_module._engine_owner_loop_id,
                db_module._engine_owner_role,
                db_module._engine_owner_loop,
            )
        )

    async def initialize_and_close() -> None:
        await db_module.init_db()
        db_module.engine.dispose.side_effect = observe_unpublished_state
        await db_module.close_db()

    asyncio.run(initialize_and_close())

    assert observed_during_dispose == [(None, None, None, None, None, None)]


def test_init_db_failure_does_not_publish_partial_engine_and_can_retry(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))
    failed_engine = MagicMock(sync_engine=MagicMock(), dispose=AsyncMock())
    retry_engine = MagicMock(sync_engine=MagicMock(), dispose=AsyncMock())
    db_module.create_async_engine.side_effect = [failed_engine, retry_engine]
    session_factory = MagicMock(side_effect=_SessionContext)
    db_module.async_sessionmaker.side_effect = [RuntimeError("session factory failed"), session_factory]

    async def fail_then_retry() -> None:
        with pytest.raises(RuntimeError, match="session factory failed"):
            await db_module.init_db()

        failed_engine.dispose.assert_awaited_once()
        assert db_module.engine is None
        assert db_module.AsyncSessionLocal is None
        assert db_module._engine_owner_pid is None
        assert db_module._engine_owner_loop_id is None
        assert db_module._engine_owner_role is None

        await db_module.init_db()

    asyncio.run(fail_then_retry())

    assert db_module.create_async_engine.call_count == 2
    assert db_module.engine is retry_engine


@pytest.mark.parametrize(
    ("database_url", "role", "pool_size"),
    [
        ("postgresql+asyncpg://user:pass@db/app", "api", 5),
        ("postgresql+asyncpg://user:pass@db/app", "celery", 1),
        ("postgresql+asyncpg://user:pass@db/app", "cli", 1),
        ("postgresql+asyncpg://user:pass@db/app", "integration", 1),
    ],
)
def test_postgresql_pool_parameters_follow_runtime_role_contract(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    role: str,
    pool_size: int,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings(database_url, role=role, pool_size=pool_size))

    asyncio.run(db_module.init_db())

    _, kwargs = db_module.create_async_engine.call_args
    assert kwargs["pool_size"] == pool_size
    assert kwargs["max_overflow"] == 0
    assert kwargs["pool_timeout"] == 30
    server_settings = kwargs["connect_args"]["server_settings"]
    assert server_settings["search_path"] == "app,public"
    assert server_settings["application_name"].startswith(f"test:{role}:")


def test_postgresql_init_db_preserves_search_path_when_adding_application_name(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        db_module,
        "settings",
        _settings("postgresql+asyncpg://user:pass@db/app", role="celery"),
    )
    monkeypatch.setattr(db_module, "get_schema_search_path", MagicMock(return_value="tenant,app,public"))

    asyncio.run(db_module.init_db())

    _, kwargs = db_module.create_async_engine.call_args
    server_settings = kwargs["connect_args"]["server_settings"]
    assert server_settings["search_path"] == "tenant,app,public"
    assert server_settings["application_name"].startswith("test:celery:")


def test_postgresql_connect_args_merge_preserves_existing_server_settings() -> None:
    from src.database.db import build_postgresql_connect_args

    existing = {
        "command_timeout": 15,
        "server_settings": {
            "search_path": "tenant,app,public",
            "statement_timeout": "5000",
            "application_name": "stale-owner",
        },
    }

    merged = build_postgresql_connect_args(
        existing,
        search_path="default,public",
        application_name="prod:celery:worker:123",
    )

    assert merged == {
        "command_timeout": 15,
        "server_settings": {
            "search_path": "tenant,app,public",
            "statement_timeout": "5000",
            "application_name": "prod:celery:worker:123",
        },
    }
    assert existing["server_settings"]["application_name"] == "stale-owner"


def test_sqlite_uses_static_pool_without_postgresql_pool_arguments(
    db_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_module, "settings", _settings("sqlite+aiosqlite:///:memory:"))

    asyncio.run(db_module.init_db())

    _, kwargs = db_module.create_async_engine.call_args
    assert kwargs["poolclass"] is StaticPool
    for forbidden in ("pool_size", "max_overflow", "pool_timeout", "connect_args"):
        assert forbidden not in kwargs
