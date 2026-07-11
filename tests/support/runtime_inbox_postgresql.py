"""RuntimeInbox PostgreSQL heavy tests 共用的隔离数据库 harness。"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import make_url

from src.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

REPO_ROOT = Path(__file__).resolve().parents[2]
SAFE_DATABASE_PREFIX = "wes_tmp_runtime_inbox_"


def database_url(database: str, *, sqlalchemy_driver: bool) -> str:
    """从显式 heavy-test URL 派生临时数据库连接串。"""

    url = make_url(os.getenv("INTEGRATION_DATABASE_URL") or settings.DATABASE_URL)
    drivername = "postgresql+asyncpg" if sqlalchemy_driver else "postgresql"
    return url.set(drivername=drivername, database=database).render_as_string(hide_password=False)


async def connect(database: str) -> asyncpg.Connection:
    """连接指定临时数据库。"""

    return await asyncpg.connect(database_url(database, sqlalchemy_driver=False))


async def _drop_database(admin: asyncpg.Connection, database: str) -> None:
    assert database.startswith(SAFE_DATABASE_PREFIX)
    quoted_database = '"' + database.replace('"', '""') + '"'
    await admin.execute(f"DROP DATABASE IF EXISTS {quoted_database} WITH (FORCE)")


@asynccontextmanager
async def temporary_database() -> AsyncIterator[tuple[str, str]]:
    """创建并强制清理安全前缀的临时数据库。"""

    database = f"{SAFE_DATABASE_PREFIX}{uuid4().hex}"
    admin = await asyncpg.connect(database_url("postgres", sqlalchemy_driver=False))
    try:
        quoted_database = '"' + database.replace('"', '""') + '"'
        await admin.execute(f"CREATE DATABASE {quoted_database}")
        yield database, database_url(database, sqlalchemy_driver=True)
    finally:
        await _drop_database(admin, database)
        await admin.close()


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


__all__ = ["connect", "database_url", "run_alembic", "temporary_database"]
