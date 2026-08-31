"""固定基线生成数据库的 FAST 安全合同。"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any
from urllib.parse import urlunsplit

import pytest

from tests.support import postgresql_heavy

_PASSWORD = "baseline-secret"
_DATABASE = "wes_baseline_generation"


def _database_url(host: str) -> str:
    return urlunsplit(("postgresql", f"wes:{_PASSWORD}@{host}:5432", "/test", "", ""))


_LOOPBACK_ENV = {
    "INTEGRATION_DATABASE_URL": _database_url("127.0.0.1"),
}


class FakeAdmin:
    def __init__(
        self,
        *,
        create_failure: bool = False,
        drop_failure: bool = False,
        remaining_databases: int = 0,
        catalog_results: list[int] | None = None,
    ) -> None:
        self.create_failure = create_failure
        self.drop_failure = drop_failure
        self.catalog_results = list(catalog_results or [0, remaining_databases])
        self.statements: list[str] = []
        self.catalog_queries: list[tuple[str, str]] = []
        self.closed = False

    async def fetchrow(self, _statement: str) -> dict[str, int | bool]:
        return {
            "can_create_database": True,
            "is_superuser": True,
            "max_connections": 20,
            "reserved_connections": 3,
            "active_connections": 1,
        }

    async def execute(self, statement: str) -> None:
        self.statements.append(statement)
        if statement.startswith("CREATE DATABASE") and self.create_failure:
            raise RuntimeError(f"create failed: {_PASSWORD}")
        if statement.startswith("DROP DATABASE") and self.drop_failure:
            raise RuntimeError(f"drop failed: {_PASSWORD}")

    async def fetchval(self, statement: str, database: str) -> int:
        self.catalog_queries.append((statement, database))
        return self.catalog_results.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        self.urls: list[str] = []

    async def connect(self, url: str) -> FakeAdmin:
        self.urls.append(url)
        return self.admin


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["db", "database.example.test"])
async def test_fixed_database_rejects_non_loopback_host_even_when_allowlisted(host: str) -> None:
    """移除固定 wrapper 的 loopback 检查时，本测试必须失败。"""

    admin = FakeAdmin()
    driver = FakeDriver(admin)
    environ = {
        "INTEGRATION_DATABASE_URL": _database_url(host),
        "INTEGRATION_DATABASE_SAFE_HOSTS": host,
    }

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^unsafe_target:"):
        async with postgresql_heavy.baseline_generation_database(environ=environ, driver=driver):
            pytest.fail("unsafe host must not reach the scenario")

    assert driver.urls == []


@pytest.mark.asyncio
async def test_fixed_database_rejects_any_other_database_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定库名校验被删除时，本测试必须失败。"""

    admin = FakeAdmin()
    driver = FakeDriver(admin)
    monkeypatch.setattr(postgresql_heavy, "BASELINE_GENERATION_DATABASE_NAME", "wes_other_database")

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^unsafe_target:"):
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            pytest.fail("wrong database name must not reach the scenario")

    assert driver.urls == []


@pytest.mark.asyncio
async def test_fixed_database_rejects_preexisting_database_without_create_or_drop() -> None:
    """固定库预先存在时，wrapper 若创建或删除该库，本测试必须失败。"""

    admin = FakeAdmin(catalog_results=[1])
    driver = FakeDriver(admin)

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^unsafe_target:") as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            pytest.fail("pre-existing database must not reach the scenario")

    assert admin.statements == []
    assert admin.catalog_queries == [
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
    ]
    assert admin.closed is True
    assert _PASSWORD not in str(raised.value)
    assert _PASSWORD not in repr(raised.value)


@pytest.mark.asyncio
async def test_fixed_database_create_failure_never_drops_unowned_database() -> None:
    """CREATE 结果失败后若猜测所有权并 DROP，本测试必须失败。"""

    admin = FakeAdmin(create_failure=True, catalog_results=[0, 1])
    driver = FakeDriver(admin)

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^create:") as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            pytest.fail("failed CREATE must not reach the scenario")

    assert admin.statements == [f'CREATE DATABASE "{_DATABASE}"']
    assert admin.catalog_queries == [
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
    ]
    assert raised.value.cleanup_diagnostics == ("unowned_database_present",)
    assert admin.closed is True
    assert _PASSWORD not in str(raised.value)
    assert _PASSWORD not in repr(raised.value)


@pytest.mark.asyncio
async def test_fixed_database_success_force_drops_and_proves_catalog_absence() -> None:
    """遗漏 DROP 或 pg_database 缺席复查时，本测试必须失败。"""

    admin = FakeAdmin()
    driver = FakeDriver(admin)

    async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver) as value:
        database, database_url = value
        assert database == _DATABASE
        assert database_url.endswith(f"/{_DATABASE}")

    assert admin.statements == [
        f'CREATE DATABASE "{_DATABASE}"',
        f'DROP DATABASE IF EXISTS "{_DATABASE}" WITH (FORCE)',
    ]
    assert admin.catalog_queries == [
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
    ]
    assert admin.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["scenario", "subprocess"])
async def test_fixed_database_failure_is_stable_and_still_cleans_up(
    failure_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """场景或子进程失败若绕过清理或泄密，本测试必须失败。"""

    admin = FakeAdmin()
    driver = FakeDriver(admin)

    def fail_subprocess(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["alembic"], output=_PASSWORD, stderr=_PASSWORD)

    monkeypatch.setattr(postgresql_heavy.subprocess, "run", fail_subprocess)

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^scenario:") as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver) as (
            _database,
            database_url,
        ):
            if failure_kind == "subprocess":
                postgresql_heavy.run_alembic("upgrade", "head", database_url=database_url)
            raise RuntimeError(f"scenario failed: {_PASSWORD}")

    captured = capsys.readouterr()
    assert _PASSWORD not in str(raised.value)
    assert _PASSWORD not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert _PASSWORD not in captured.out
    assert _PASSWORD not in captured.err
    assert admin.statements[-1] == f'DROP DATABASE IF EXISTS "{_DATABASE}" WITH (FORCE)'
    assert len(admin.catalog_queries) == 2
    assert admin.closed is True


@pytest.mark.asyncio
async def test_fixed_database_cancellation_waits_for_cleanup() -> None:
    """取消路径若中断清理，本测试必须失败。"""

    admin = FakeAdmin()
    driver = FakeDriver(admin)

    with pytest.raises(asyncio.CancelledError):
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            raise asyncio.CancelledError

    assert admin.statements[-1] == f'DROP DATABASE IF EXISTS "{_DATABASE}" WITH (FORCE)'
    assert len(admin.catalog_queries) == 2
    assert admin.closed is True


@pytest.mark.asyncio
async def test_fixed_database_cancellation_during_create_waits_for_success_then_cleans() -> None:
    """CREATE await 中取消若抢在明确结果前决定 ownership，本测试必须失败。"""

    class DelayedCreateAdmin(FakeAdmin):
        def __init__(self) -> None:
            super().__init__()
            self.create_started = asyncio.Event()
            self.allow_create = asyncio.Event()

        async def execute(self, statement: str) -> None:
            self.statements.append(statement)
            if statement.startswith("CREATE DATABASE"):
                self.create_started.set()
                await self.allow_create.wait()

    admin = DelayedCreateAdmin()
    driver = FakeDriver(admin)
    scenario_reached = False

    async def run_scenario() -> None:
        nonlocal scenario_reached
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            scenario_reached = True

    caller = asyncio.create_task(run_scenario())
    await admin.create_started.wait()
    caller.cancel("caller_cancelled_during_create")
    await asyncio.sleep(0)
    admin.allow_create.set()

    with pytest.raises(asyncio.CancelledError) as raised:
        await caller

    assert raised.value.args == ("caller_cancelled_during_create",)
    assert scenario_reached is False
    assert admin.statements == [
        f'CREATE DATABASE "{_DATABASE}"',
        f'DROP DATABASE IF EXISTS "{_DATABASE}" WITH (FORCE)',
    ]
    assert admin.catalog_queries == [
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
        ("SELECT count(*)::integer FROM pg_database WHERE datname = $1", _DATABASE),
    ]
    assert admin.closed is True


@pytest.mark.asyncio
async def test_fixed_database_drop_failure_fails_closed_without_leaking_credentials() -> None:
    """DROP 异常若被忽略或原样暴露，本测试必须失败。"""

    admin = FakeAdmin(drop_failure=True)
    driver = FakeDriver(admin)

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^cleanup:") as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            pass

    assert raised.value.cleanup_diagnostics == ("drop_failed",)
    assert _PASSWORD not in str(raised.value)
    assert _PASSWORD not in repr(raised.value)
    assert len(admin.catalog_queries) == 2
    assert admin.closed is True


@pytest.mark.asyncio
async def test_fixed_database_preserves_stable_primary_failure_with_cleanup_diagnostics() -> None:
    """清理失败若覆盖原 stable failure，本测试必须失败。"""

    admin = FakeAdmin(drop_failure=True)
    driver = FakeDriver(admin)
    primary = postgresql_heavy.HeavyHarnessError("manifest", "catalog 生成失败")

    with pytest.raises(postgresql_heavy.HeavyHarnessError) as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            raise primary

    assert raised.value is primary
    assert raised.value.code == "manifest"
    assert raised.value.cleanup_diagnostics == ("drop_failed",)
    assert admin.closed is True


@pytest.mark.asyncio
async def test_fixed_database_post_drop_presence_fails_closed() -> None:
    """DROP 后 catalog 仍有同名数据库时，本测试必须失败。"""

    admin = FakeAdmin(remaining_databases=1)
    driver = FakeDriver(admin)

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^cleanup:") as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            pass

    assert raised.value.cleanup_diagnostics == ("database_still_present",)
    assert admin.closed is True


@pytest.mark.asyncio
async def test_fixed_database_post_drop_check_failure_is_stable_and_does_not_leak_credentials() -> None:
    """catalog 查询异常必须转为稳定诊断，且不暴露 driver 原始错误。"""

    class CatalogFailureAdmin(FakeAdmin):
        calls = 0

        async def fetchval(self, _statement: str, _database: str) -> int:
            self.calls += 1
            if self.calls == 1:
                return 0
            raise RuntimeError(f"catalog failed: {_PASSWORD}")

    admin = CatalogFailureAdmin()
    driver = FakeDriver(admin)

    with pytest.raises(postgresql_heavy.HeavyHarnessError, match=r"^cleanup:") as raised:
        async with postgresql_heavy.baseline_generation_database(environ=_LOOPBACK_ENV, driver=driver):
            pass

    assert raised.value.cleanup_diagnostics == ("post_drop_check_failed",)
    assert _PASSWORD not in str(raised.value)
    assert _PASSWORD not in repr(raised.value)
    assert admin.closed is True


@pytest.mark.asyncio
async def test_generic_temporary_database_keeps_existing_random_name_cleanup_behavior() -> None:
    """固定 wrapper 修复若改变 generic random database 行为，本测试必须失败。"""

    admin = FakeAdmin()
    driver = FakeDriver(admin)
    database = f"{postgresql_heavy.SAFE_DATABASE_PREFIX}{'a' * 32}"

    async with postgresql_heavy.temporary_database(
        environ=_LOOPBACK_ENV,
        driver=driver,
        database_name_factory=lambda: database,
    ) as yielded:
        assert yielded[0] == database

    assert admin.statements == [
        f'CREATE DATABASE "{database}"',
        f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
    ]
    assert admin.catalog_queries == []
    assert admin.closed is True


def test_alembic_receives_database_url_only_through_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL 被放进命令参数或输出时，本测试必须失败。"""

    database_url = f"postgresql+asyncpg://wes:{_PASSWORD}@127.0.0.1:5432/{_DATABASE}"
    observed: dict[str, Any] = {}

    def capture_subprocess(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(postgresql_heavy.subprocess, "run", capture_subprocess)

    result = postgresql_heavy.run_alembic("upgrade", "head", database_url=database_url)

    assert database_url not in observed["command"]
    assert observed["env"]["ALEMBIC_DATABASE_URL"] == database_url
    assert result.stdout == ""
    assert result.stderr == ""
