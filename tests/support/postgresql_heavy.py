"""PostgreSQL HEAVY tests 共用的隔离数据库 harness。"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import URL, make_url

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
SAFE_DATABASE_PREFIX = "wes_tmp_heavy_"
SAFE_TEMPLATE_DATABASE_NAME = "wes_tmp_heavy_template"
BASELINE_GENERATION_DATABASE_NAME = "wes_baseline_generation"
REQUIRED_FREE_CONNECTION_SLOTS = 3
DEFAULT_SAFE_DATABASE_HOSTS = frozenset({"localhost", "db"})
_BASELINE_GENERATION_DATABASE_NAME = "wes_baseline_generation"
_BASELINE_GENERATION_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SAFE_DATABASE_PATTERN = re.compile(rf"{re.escape(SAFE_DATABASE_PREFIX)}[0-9a-f]{{32}}\Z")
_SAFE_HOSTNAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_PREFLIGHT_SQL = """
SELECT
    (role.rolcreatedb OR role.rolsuper) AS can_create_database,
    role.rolsuper AS is_superuser,
    current_setting('max_connections')::integer AS max_connections,
    current_setting('superuser_reserved_connections')::integer AS reserved_connections,
    (SELECT count(*)::integer FROM pg_stat_activity) AS active_connections
FROM pg_roles AS role
WHERE role.rolname = current_user
"""
_DATABASE_ABSENCE_SQL = "SELECT count(*)::integer FROM pg_database WHERE datname = $1"


class HeavyHarnessError(RuntimeError):
    """不泄露连接细节的 stable heavy-harness failure。"""

    def __init__(self, code: str, message: str, *, cleanup_diagnostics: tuple[str, ...] = ()) -> None:
        self.code = code
        self.cleanup_diagnostics = cleanup_diagnostics
        super().__init__(f"{code}: {message}")

    def attach_cleanup(self, diagnostics: tuple[str, ...]) -> None:
        self.cleanup_diagnostics = diagnostics


@dataclass(slots=True)
class PostgreSQLPreflight:
    """已验证且复用到 create/drop 的 admin 连接。"""

    admin: Any = field(repr=False)
    base_url: URL = field(repr=False)
    available_connection_slots: int
    is_superuser: bool

    async def close(self) -> None:
        diagnostics, interrupted_by = await _wait_for_connection_close(self.admin)
        if interrupted_by is not None:
            _attach_cleanup(interrupted_by, diagnostics)
            raise interrupted_by from None
        if diagnostics:
            raise HeavyHarnessError(
                "cleanup",
                "PostgreSQL admin 连接清理失败",
                cleanup_diagnostics=diagnostics,
            )


def _safe_source_database(database: str) -> bool:
    normalized = database.lower()
    return (
        normalized in {"postgres", "template1", "test"}
        or normalized.startswith("test_")
        or normalized.endswith("_test")
    )


def _normalize_host(host: str) -> str:
    candidate = host.strip().removesuffix(".")
    if not candidate:
        raise HeavyHarnessError("unsafe_target", "integration URL host 不在安全 allowlist")
    try:
        return ipaddress.ip_address(candidate).compressed.lower()
    except ValueError:
        pass
    try:
        normalized = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise HeavyHarnessError("unsafe_target", "integration URL host 不在安全 allowlist") from None
    if _SAFE_HOSTNAME_PATTERN.fullmatch(normalized) is None or ".." in normalized:
        raise HeavyHarnessError("unsafe_target", "integration URL host 不在安全 allowlist")
    return normalized


def _safe_database_hosts(source: Mapping[str, str], safe_hosts: Collection[str] | None) -> frozenset[str]:
    configured_hosts = list(safe_hosts or ())
    configured_hosts.extend(source.get("INTEGRATION_DATABASE_SAFE_HOSTS", "").split(","))
    return frozenset(_normalize_host(host) for host in configured_hosts if host.strip())


def _is_safe_database_host(host: str, configured_hosts: frozenset[str]) -> bool:
    normalized = _normalize_host(host)
    if normalized in DEFAULT_SAFE_DATABASE_HOSTS or normalized in configured_hosts:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _integration_url(
    environ: Mapping[str, str] | None = None,
    *,
    safe_hosts: Collection[str] | None = None,
) -> URL:
    source = os.environ if environ is None else environ
    raw_url = source.get("INTEGRATION_DATABASE_URL", "").strip()
    if not raw_url:
        raise HeavyHarnessError("missing_url", "需要显式配置 PostgreSQL integration URL")
    try:
        url = make_url(raw_url)
    except Exception:
        raise HeavyHarnessError("invalid_url", "integration URL 无法解析") from None
    if url.get_backend_name() != "postgresql" or not url.database:
        raise HeavyHarnessError("invalid_url", "integration URL 必须指向 PostgreSQL 数据库")
    configured_hosts = _safe_database_hosts(source, safe_hosts)
    if not url.host or not _is_safe_database_host(url.host, configured_hosts):
        raise HeavyHarnessError("unsafe_target", "integration URL host 不在安全 allowlist")
    if not _safe_source_database(url.database):
        raise HeavyHarnessError("unsafe_target", "integration URL 未指向隔离的 admin/test 数据库")
    return url


def _render_url(url: URL, *, database: str, sqlalchemy_driver: bool) -> str:
    drivername = "postgresql+asyncpg" if sqlalchemy_driver else "postgresql"
    return url.set(drivername=drivername, database=database).render_as_string(hide_password=False)


def database_url(database: str, *, sqlalchemy_driver: bool) -> str:
    """从显式 heavy-test URL 派生数据库连接串。"""

    return _render_url(_integration_url(), database=database, sqlalchemy_driver=sqlalchemy_driver)


async def connect(database: str) -> asyncpg.Connection:
    """连接指定临时数据库。"""

    return await asyncpg.connect(database_url(database, sqlalchemy_driver=False))


async def _close_connection(connection: Any) -> tuple[str, ...]:
    try:
        await connection.close()
    except Exception:
        return ("close_failed",)
    return ()


async def _wait_for_connection_close(connection: Any) -> tuple[tuple[str, ...], asyncio.CancelledError | None]:
    close_task = asyncio.create_task(_close_connection(connection), name="close-postgresql-admin")
    interrupted_by: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(close_task), interrupted_by
        except asyncio.CancelledError as exc:
            if close_task.cancelled():
                raise
            if interrupted_by is None:
                interrupted_by = exc


async def _raise_after_admin_close(admin: Any, primary_error: BaseException) -> None:
    diagnostics, _secondary_cancellation = await _wait_for_connection_close(admin)
    _attach_cleanup(primary_error, diagnostics)
    raise primary_error from None


async def preflight(
    *,
    environ: Mapping[str, str] | None = None,
    driver: Any = asyncpg,
    required_free_slots: int = REQUIRED_FREE_CONNECTION_SLOTS,
    safe_hosts: Collection[str] | None = None,
) -> PostgreSQLPreflight:
    """验证显式 URL、认证、CREATEDB 权限与当前连接容量。"""

    url = _integration_url(environ, safe_hosts=safe_hosts)
    admin_url = _render_url(url, database=url.database or "postgres", sqlalchemy_driver=False)
    try:
        admin = await driver.connect(admin_url)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "53300":
            raise HeavyHarnessError("capacity", "PostgreSQL 当前连接容量已耗尽") from None
        raise HeavyHarnessError("auth", "无法连接或认证 PostgreSQL admin/test 数据库") from None

    try:
        row = await admin.fetchrow(_PREFLIGHT_SQL)
        if row is None or not bool(row["can_create_database"]):
            raise HeavyHarnessError("permission", "PostgreSQL 账号缺少 CREATEDB 权限")
        is_superuser = bool(row["is_superuser"])
        max_connections = int(row["max_connections"])
        reserved_connections = int(row["reserved_connections"])
        active_connections = int(row["active_connections"])
        usable_connections = max_connections if is_superuser else max_connections - reserved_connections
        available_connection_slots = usable_connections - active_connections
        if available_connection_slots < required_free_slots:
            raise HeavyHarnessError("capacity", "PostgreSQL 当前剩余连接槽不足")
    except asyncio.CancelledError as exc:
        await _raise_after_admin_close(admin, exc)
    except HeavyHarnessError as exc:
        await _raise_after_admin_close(admin, exc)
    except Exception:
        await _raise_after_admin_close(
            admin,
            HeavyHarnessError("permission", "无法验证 PostgreSQL 权限或容量"),
        )

    return PostgreSQLPreflight(
        admin=admin,
        base_url=url,
        available_connection_slots=available_connection_slots,
        is_superuser=is_superuser,
    )


def _validate_temporary_database_name(database: str) -> None:
    if _SAFE_DATABASE_PATTERN.fullmatch(database) is None:
        raise HeavyHarnessError("unsafe_target", "拒绝创建或删除非随机安全前缀的数据库")


def _validate_template_database_name(database: str) -> None:
    if database != SAFE_TEMPLATE_DATABASE_NAME:
        raise HeavyHarnessError("unsafe_target", "拒绝从非固定 HEAVY 模板创建数据库")


def _validate_baseline_generation_database_name(database: str) -> None:
    if database != _BASELINE_GENERATION_DATABASE_NAME:
        raise HeavyHarnessError("unsafe_target", "拒绝创建或删除非固定基线生成数据库")


def _quote_database(database: str) -> str:
    return '"' + database.replace('"', '""') + '"'


async def _drop_database(admin: Any, database: str) -> None:
    _validate_temporary_database_name(database)
    await admin.execute(f"DROP DATABASE IF EXISTS {_quote_database(database)} WITH (FORCE)")


async def _drop_baseline_generation_database(admin: Any, database: str) -> None:
    _validate_baseline_generation_database_name(database)
    await admin.execute(f"DROP DATABASE IF EXISTS {_quote_database(database)} WITH (FORCE)")


async def _baseline_generation_database_count(admin: Any, database: str) -> int:
    _validate_baseline_generation_database_name(database)
    try:
        return int(await admin.fetchval(_DATABASE_ABSENCE_SQL, database))
    except asyncio.CancelledError:
        raise
    except Exception:
        raise HeavyHarnessError("catalog", "无法确认固定基线生成数据库状态") from None


async def _baseline_generation_absence_diagnostic(admin: Any, database: str) -> str | None:
    try:
        remaining_databases = await _baseline_generation_database_count(admin, database)
    except HeavyHarnessError:
        return "post_drop_check_failed"
    return "database_still_present" if remaining_databases != 0 else None


async def _baseline_generation_unowned_diagnostics(admin: Any, database: str) -> tuple[str, ...]:
    try:
        remaining_databases = await _baseline_generation_database_count(admin, database)
    except HeavyHarnessError:
        return ("unowned_database_check_failed",)
    if remaining_databases != 0:
        return ("unowned_database_present",)
    return ()


async def _create_baseline_generation_database(admin: Any, database: str) -> None:
    _validate_baseline_generation_database_name(database)
    await admin.execute(f"CREATE DATABASE {_quote_database(database)}")


async def _wait_for_baseline_generation_create(
    admin: Any,
    database: str,
) -> tuple[bool, asyncio.CancelledError | None, HeavyHarnessError | None]:
    create_task = asyncio.create_task(
        _create_baseline_generation_database(admin, database),
        name="create-baseline-generation-database",
    )
    interrupted_by: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(create_task)
        except asyncio.CancelledError as exc:
            if create_task.cancelled():
                return False, interrupted_by or exc, None
            if interrupted_by is None:
                interrupted_by = exc
        except Exception:
            return False, interrupted_by, HeavyHarnessError("create", "创建固定基线生成数据库失败")
        else:
            return True, interrupted_by, None


def _random_database_name() -> str:
    return f"{SAFE_DATABASE_PREFIX}{uuid4().hex}"


def _attach_cleanup(primary_error: BaseException, diagnostics: tuple[str, ...]) -> None:
    if not diagnostics:
        return
    if isinstance(primary_error, HeavyHarnessError):
        primary_error.attach_cleanup(diagnostics)
    else:
        primary_error.add_note(f"heavy_harness_cleanup={','.join(diagnostics)}")


async def _cleanup_database(
    checked: PostgreSQLPreflight,
    database: str,
    *,
    drop_requested: bool,
    drop_database: Callable[[Any, str], Awaitable[None]] = _drop_database,
    post_drop_diagnostic: Callable[[Any, str], Awaitable[str | None]] | None = None,
) -> tuple[str, ...]:
    diagnostics: list[str] = []
    try:
        if drop_requested:
            try:
                await drop_database(checked.admin, database)
            except Exception:
                diagnostics.append("drop_failed")
            if post_drop_diagnostic is not None:
                diagnostic = await post_drop_diagnostic(checked.admin, database)
                if diagnostic is not None:
                    diagnostics.append(diagnostic)
    finally:
        try:
            await checked.close()
        except Exception:
            diagnostics.append("close_failed")
    return tuple(diagnostics)


async def _wait_for_cleanup(
    checked: PostgreSQLPreflight,
    database: str,
    *,
    drop_requested: bool,
    drop_database: Callable[[Any, str], Awaitable[None]] = _drop_database,
    post_drop_diagnostic: Callable[[Any, str], Awaitable[str | None]] | None = None,
) -> tuple[tuple[str, ...], asyncio.CancelledError | None]:
    cleanup_task = asyncio.create_task(
        _cleanup_database(
            checked,
            database,
            drop_requested=drop_requested,
            drop_database=drop_database,
            post_drop_diagnostic=post_drop_diagnostic,
        ),
        name=f"cleanup-{database}",
    )
    interrupted_by: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(cleanup_task), interrupted_by
        except asyncio.CancelledError as exc:
            if cleanup_task.cancelled():
                raise
            if interrupted_by is None:
                interrupted_by = exc


@asynccontextmanager
async def baseline_generation_database(
    *,
    environ: Mapping[str, str] | None = None,
    driver: Any = asyncpg,
    required_free_slots: int = REQUIRED_FREE_CONNECTION_SLOTS,
) -> AsyncIterator[tuple[str, str]]:
    """创建固定用途基线生成库，并在每条退出路径证明其已删除。"""

    database = BASELINE_GENERATION_DATABASE_NAME
    _validate_baseline_generation_database_name(database)
    source_url = _integration_url(environ)
    if _normalize_host(source_url.host or "") not in _BASELINE_GENERATION_HOSTS:
        raise HeavyHarnessError("unsafe_target", "基线生成数据库只允许 loopback PostgreSQL host")
    checked = await preflight(
        environ=environ,
        driver=driver,
        required_free_slots=required_free_slots,
    )
    primary_error: BaseException | None = None
    owns_database = False
    ownership_diagnostics: tuple[str, ...] = ()
    cleanup_diagnostics: tuple[str, ...] = ()
    cleanup_cancellation: asyncio.CancelledError | None = None

    try:
        try:
            if await _baseline_generation_database_count(checked.admin, database) != 0:
                primary_error = HeavyHarnessError("unsafe_target", "固定基线生成数据库已存在")
        except asyncio.CancelledError as exc:
            primary_error = exc
        except HeavyHarnessError as exc:
            primary_error = exc

        if primary_error is None:
            owns_database, create_cancellation, create_error = await _wait_for_baseline_generation_create(
                checked.admin,
                database,
            )
            if create_cancellation is not None:
                primary_error = create_cancellation
            elif create_error is not None:
                primary_error = create_error
            if not owns_database:
                ownership_diagnostics = await _baseline_generation_unowned_diagnostics(checked.admin, database)

        if primary_error is None:
            try:
                yield database, _render_url(checked.base_url, database=database, sqlalchemy_driver=True)
            except asyncio.CancelledError as exc:
                primary_error = exc
            except HeavyHarnessError as exc:
                primary_error = exc
            except Exception:
                primary_error = HeavyHarnessError("scenario", "基线生成场景执行失败")
    finally:
        cleanup_diagnostics, cleanup_cancellation = await _wait_for_cleanup(
            checked,
            database,
            drop_requested=owns_database,
            drop_database=_drop_baseline_generation_database,
            post_drop_diagnostic=_baseline_generation_absence_diagnostic,
        )

    cleanup_diagnostics = ownership_diagnostics + cleanup_diagnostics
    if cleanup_cancellation is not None and primary_error is None:
        primary_error = cleanup_cancellation
    if primary_error is not None:
        _attach_cleanup(primary_error, cleanup_diagnostics)
        raise primary_error from None
    if cleanup_diagnostics:
        raise HeavyHarnessError(
            "cleanup",
            "固定基线生成数据库清理失败",
            cleanup_diagnostics=cleanup_diagnostics,
        )


@asynccontextmanager
async def temporary_database(
    *,
    environ: Mapping[str, str] | None = None,
    driver: Any = asyncpg,
    database_name_factory: Callable[[], str] = _random_database_name,
    safe_hosts: Collection[str] | None = None,
    required_free_slots: int = REQUIRED_FREE_CONNECTION_SLOTS,
    template_database: str | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """创建隔离临时数据库，并在成功、失败或取消后强制清理。"""

    database = database_name_factory()
    _validate_temporary_database_name(database)
    if template_database is not None:
        _validate_template_database_name(template_database)
    checked = await preflight(
        environ=environ,
        driver=driver,
        required_free_slots=required_free_slots,
        safe_hosts=safe_hosts,
    )
    primary_error: BaseException | None = None
    primary_cause: BaseException | None = None
    create_attempted = False
    cleanup_diagnostics: tuple[str, ...] = ()
    cleanup_cancellation: asyncio.CancelledError | None = None

    # preflight -> CREATE -> yield scenario -> DROP -> close admin
    #                \_____ every exit path reaches cleanup _____/
    try:
        try:
            create_attempted = True
            create_statement = f"CREATE DATABASE {_quote_database(database)}"
            if template_database is not None:
                create_statement += f" TEMPLATE {_quote_database(template_database)}"
            await checked.admin.execute(create_statement)
        except asyncio.CancelledError as exc:
            primary_error = exc
        except Exception:
            primary_error = HeavyHarnessError("create", "创建 PostgreSQL 临时数据库失败")

        if primary_error is None:
            try:
                yield database, _render_url(checked.base_url, database=database, sqlalchemy_driver=True)
            except asyncio.CancelledError as exc:
                primary_error = exc
            except Exception as exc:
                primary_error = HeavyHarnessError("scenario", "PostgreSQL heavy scenario 执行失败")
                primary_cause = exc
    finally:
        cleanup_diagnostics, cleanup_cancellation = await _wait_for_cleanup(
            checked,
            database,
            drop_requested=create_attempted,
        )

    if cleanup_cancellation is not None and primary_error is None:
        primary_error = cleanup_cancellation
    diagnostics = cleanup_diagnostics
    if primary_error is not None:
        _attach_cleanup(primary_error, diagnostics)
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error
    if diagnostics:
        raise HeavyHarnessError("cleanup", "PostgreSQL 临时数据库清理失败", cleanup_diagnostics=diagnostics)


def run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    """让 Alembic 明确连接临时数据库。"""

    env = os.environ.copy()
    env["ALEMBIC_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


__all__ = [
    "BASELINE_GENERATION_DATABASE_NAME",
    "SAFE_DATABASE_PREFIX",
    "SAFE_TEMPLATE_DATABASE_NAME",
    "HeavyHarnessError",
    "PostgreSQLPreflight",
    "baseline_generation_database",
    "connect",
    "database_url",
    "preflight",
    "run_alembic",
    "temporary_database",
]
