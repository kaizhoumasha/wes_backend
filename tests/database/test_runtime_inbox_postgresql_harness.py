"""RuntimeInbox PostgreSQL heavy harness 的快速单元合同。"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import pytest

from tests.support.runtime_inbox_postgresql import (
    SAFE_DATABASE_PREFIX,
    HeavyHarnessError,
    preflight,
    temporary_database,
)

SAFE_URL = "postgresql://runner:top-secret@localhost/test_runtime"
TEMP_DATABASE = f"{SAFE_DATABASE_PREFIX}{'a' * 32}"


@dataclass(slots=True)
class _FakeConnection:
    preflight_row: dict[str, object] = field(
        default_factory=lambda: {
            "can_create_database": True,
            "is_superuser": False,
            "max_connections": 20,
            "reserved_connections": 3,
            "active_connections": 2,
        }
    )
    fail_create: BaseException | None = None
    fail_drop: BaseException | None = None
    drop_started: asyncio.Event | None = None
    allow_drop: asyncio.Event | None = None
    statements: list[str] = field(default_factory=list)
    drop_completed: bool = False
    closed: bool = False

    async def fetchrow(self, _query: str) -> dict[str, object]:
        return self.preflight_row

    async def execute(self, statement: str) -> str:
        self.statements.append(statement)
        if statement.startswith("CREATE DATABASE") and self.fail_create is not None:
            raise self.fail_create
        if statement.startswith("DROP DATABASE") and self.fail_drop is not None:
            raise self.fail_drop
        if statement.startswith("DROP DATABASE"):
            if self.drop_started is not None:
                self.drop_started.set()
            if self.allow_drop is not None:
                await self.allow_drop.wait()
            self.drop_completed = True
        return "OK"

    async def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class _FakeDriver:
    connection: _FakeConnection = field(default_factory=_FakeConnection)
    connect_error: BaseException | None = None
    urls: list[str] = field(default_factory=list)

    async def connect(self, url: str) -> _FakeConnection:
        self.urls.append(url)
        if self.connect_error is not None:
            raise self.connect_error
        return self.connection


class _FakePostgreSQLConnectError(RuntimeError):
    def __init__(self, message: str, *, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        super().__init__(message)


@pytest.mark.parametrize(
    ("environ", "expected_code"),
    [
        ({}, "missing_url"),
        ({"INTEGRATION_DATABASE_URL": "not-a-url"}, "invalid_url"),
        ({"INTEGRATION_DATABASE_URL": "mysql://runner@localhost/test_runtime"}, "invalid_url"),
        ({"INTEGRATION_DATABASE_URL": "postgresql://runner@localhost/wes_business"}, "unsafe_target"),
    ],
)
def test_preflight_rejects_missing_invalid_and_unsafe_urls_without_leaking_values(
    environ: dict[str, str], expected_code: str
) -> None:
    async def scenario() -> None:
        with pytest.raises(HeavyHarnessError) as exc_info:
            await preflight(environ=environ, driver=_FakeDriver())

        assert exc_info.value.code == expected_code
        assert "runner" not in str(exc_info.value)
        assert "top-secret" not in str(exc_info.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://runner:top-secret@localhost/test_runtime",
        "postgresql://runner:top-secret@127.42.0.1/test_runtime",
        "postgresql://runner:top-secret@[::1]/test_runtime",
        "postgresql://runner:top-secret@db/test_runtime",
    ],
)
def test_preflight_accepts_only_default_local_loopback_and_project_docker_hosts(url: str) -> None:
    async def scenario() -> None:
        checked = await preflight(environ={"INTEGRATION_DATABASE_URL": url}, driver=_FakeDriver())
        await checked.close()

    asyncio.run(scenario())


def test_preflight_rejects_remote_host_and_exact_allowlist_prevents_host_bypasses() -> None:
    async def assert_unsafe(url: str, *, safe_hosts: set[str] | None = None, env_hosts: str | None = None) -> None:
        environ = {"INTEGRATION_DATABASE_URL": url}
        if env_hosts is not None:
            environ["INTEGRATION_DATABASE_SAFE_HOSTS"] = env_hosts
        with pytest.raises(HeavyHarnessError) as exc_info:
            await preflight(environ=environ, driver=_FakeDriver(), safe_hosts=safe_hosts)
        assert exc_info.value.code == "unsafe_target"
        rendered_traceback = "".join(
            traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.value.__traceback__)
        )
        assert "prod.example" not in rendered_traceback
        assert "top-secret" not in rendered_traceback

    async def scenario() -> None:
        await assert_unsafe("postgresql://runner:top-secret@prod.example/postgres")
        await assert_unsafe(
            "postgresql://runner:top-secret@ci-db.evil:6543/test_runtime",
            safe_hosts={"ci-db"},
        )
        await assert_unsafe(
            "postgresql://runner:top-secret@ci-db:6543/test_runtime",
            env_hosts="ci-db:6543",
        )

        parameter_driver = _FakeDriver()
        parameter_checked = await preflight(
            environ={"INTEGRATION_DATABASE_URL": "postgresql://runner:top-secret@CI-DB:6543/test_runtime"},
            driver=parameter_driver,
            safe_hosts={"ci-db"},
        )
        await parameter_checked.close()

        environment_driver = _FakeDriver()
        environment_checked = await preflight(
            environ={
                "INTEGRATION_DATABASE_URL": "postgresql://runner:top-secret@[2001:db8::7]:6543/test_runtime",
                "INTEGRATION_DATABASE_SAFE_HOSTS": "ci-db, 2001:0db8:0:0:0:0:0:7",
            },
            driver=environment_driver,
        )
        await environment_checked.close()

    asyncio.run(scenario())


def test_preflight_classifies_auth_permission_and_capacity_failures() -> None:
    async def assert_failure(driver: _FakeDriver, expected_code: str) -> None:
        with pytest.raises(HeavyHarnessError) as exc_info:
            await preflight(environ={"INTEGRATION_DATABASE_URL": SAFE_URL}, driver=driver)
        assert exc_info.value.code == expected_code
        rendered_traceback = "".join(
            traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.value.__traceback__)
        )
        assert "top-secret" not in rendered_traceback

    async def scenario() -> None:
        await assert_failure(
            _FakeDriver(connect_error=asyncpg.InvalidPasswordError("top-secret authentication detail")),
            "auth",
        )
        await assert_failure(
            _FakeDriver(
                connect_error=_FakePostgreSQLConnectError(
                    "top-secret too many clients detail",
                    sqlstate="53300",
                )
            ),
            "capacity",
        )

        permission_connection = _FakeConnection()
        permission_connection.preflight_row["can_create_database"] = False
        permission_driver = _FakeDriver(connection=permission_connection)
        await assert_failure(permission_driver, "permission")
        assert permission_connection.closed

        capacity_connection = _FakeConnection()
        capacity_connection.preflight_row.update(max_connections=8, reserved_connections=3, active_connections=4)
        capacity_driver = _FakeDriver(connection=capacity_connection)
        await assert_failure(capacity_driver, "capacity")
        assert capacity_connection.closed

    asyncio.run(scenario())


def test_preflight_reuses_one_admin_connection_and_does_not_require_superuser() -> None:
    async def scenario() -> None:
        driver = _FakeDriver()
        result = await preflight(environ={"INTEGRATION_DATABASE_URL": SAFE_URL}, driver=driver)
        try:
            assert len(driver.urls) == 1
            assert result.available_connection_slots == 15
            assert result.is_superuser is False
        finally:
            await result.close()
        assert driver.connection.closed

    asyncio.run(scenario())


def test_temporary_database_success_uses_one_admin_connection_and_safe_lifecycle() -> None:
    async def scenario() -> None:
        driver = _FakeDriver()
        async with temporary_database(
            environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
            driver=driver,
            database_name_factory=lambda: TEMP_DATABASE,
        ) as (database, url):
            assert database == TEMP_DATABASE
            assert database in url
            assert driver.connection.statements == [f'CREATE DATABASE "{TEMP_DATABASE}"']

        assert len(driver.urls) == 1
        assert driver.connection.statements[-1] == f'DROP DATABASE IF EXISTS "{TEMP_DATABASE}" WITH (FORCE)'
        assert driver.connection.closed

    asyncio.run(scenario())


def test_temporary_database_classifies_create_scenario_and_cleanup_failures() -> None:
    async def scenario() -> None:
        create_driver = _FakeDriver(connection=_FakeConnection(fail_create=RuntimeError("secret create detail")))
        with pytest.raises(HeavyHarnessError) as create_error:
            async with temporary_database(
                environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                driver=create_driver,
                database_name_factory=lambda: TEMP_DATABASE,
            ):
                pytest.fail("create failure must not yield")
        assert create_error.value.code == "create"
        rendered_traceback = "".join(
            traceback.format_exception(type(create_error.value), create_error.value, create_error.value.__traceback__)
        )
        assert "secret create detail" not in rendered_traceback
        assert any(statement.startswith("DROP DATABASE") for statement in create_driver.connection.statements)

        with pytest.raises(HeavyHarnessError) as scenario_error:
            async with temporary_database(
                environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                driver=_FakeDriver(),
                database_name_factory=lambda: TEMP_DATABASE,
            ):
                raise ValueError("secret scenario detail")
        assert scenario_error.value.code == "scenario"
        assert isinstance(scenario_error.value.__cause__, ValueError)
        assert "secret" not in str(scenario_error.value)

        cleanup_driver = _FakeDriver(connection=_FakeConnection(fail_drop=RuntimeError("secret cleanup detail")))
        with pytest.raises(HeavyHarnessError) as cleanup_error:
            async with temporary_database(
                environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                driver=cleanup_driver,
                database_name_factory=lambda: TEMP_DATABASE,
            ):
                pass
        assert cleanup_error.value.code == "cleanup"
        assert "secret" not in str(cleanup_error.value)

    asyncio.run(scenario())


def test_cleanup_failure_keeps_primary_scenario_and_cancellation_errors() -> None:
    async def scenario() -> None:
        scenario_driver = _FakeDriver(connection=_FakeConnection(fail_drop=RuntimeError("drop detail")))
        with pytest.raises(HeavyHarnessError) as scenario_error:
            async with temporary_database(
                environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                driver=scenario_driver,
                database_name_factory=lambda: TEMP_DATABASE,
            ):
                raise LookupError("primary detail")
        assert scenario_error.value.code == "scenario"
        assert scenario_error.value.cleanup_diagnostics == ("drop_failed",)
        assert isinstance(scenario_error.value.__cause__, LookupError)

        cancel_driver = _FakeDriver(connection=_FakeConnection(fail_drop=RuntimeError("drop detail")))
        with pytest.raises(asyncio.CancelledError) as cancel_error:
            async with temporary_database(
                environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                driver=cancel_driver,
                database_name_factory=lambda: TEMP_DATABASE,
            ):
                raise asyncio.CancelledError
        assert any("cleanup=drop_failed" in note for note in getattr(cancel_error.value, "__notes__", ()))
        assert cancel_driver.connection.closed

    asyncio.run(scenario())


def test_external_cancellation_during_drop_waits_for_cleanup_and_preserves_cancelled_error() -> None:
    async def scenario() -> None:
        drop_started = asyncio.Event()
        allow_drop = asyncio.Event()
        connection = _FakeConnection(drop_started=drop_started, allow_drop=allow_drop)
        driver = _FakeDriver(connection=connection)

        async def use_database() -> None:
            async with temporary_database(
                environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                driver=driver,
                database_name_factory=lambda: TEMP_DATABASE,
            ):
                pass

        task = asyncio.create_task(use_database())
        await drop_started.wait()
        task.cancel("cancel-during-drop")
        await asyncio.sleep(0)
        assert not task.done()

        allow_drop.set()
        with pytest.raises(asyncio.CancelledError) as cancel_error:
            await task
        assert cancel_error.value.args == ("cancel-during-drop",)
        assert connection.drop_completed
        assert connection.closed

    asyncio.run(scenario())


def test_temporary_database_rejects_non_random_or_business_drop_targets_before_connecting() -> None:
    async def scenario() -> None:
        for target in ("wes_business", SAFE_DATABASE_PREFIX, f"{SAFE_DATABASE_PREFIX}not-hex"):
            driver = _FakeDriver()
            with pytest.raises(HeavyHarnessError) as exc_info:
                async with temporary_database(
                    environ={"INTEGRATION_DATABASE_URL": SAFE_URL},
                    driver=driver,
                    database_name_factory=lambda target=target: target,
                ):
                    pytest.fail("unsafe target must not yield")
            assert exc_info.value.code == "unsafe_target"
            assert driver.urls == []

    asyncio.run(scenario())
