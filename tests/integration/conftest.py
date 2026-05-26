from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# 预加载外键目标模型，避免 SQLModel 在 flush 时出现 NoReferencedTableError。
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.sys.models import SystemOutbox
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.session import WorklineSession
from src.app.workline.models.timeline import WorklineTimeline
from src.app.workline.models.workline import WorkLine
from src.core.conf import settings
from src.database.schema_conf import get_schema_search_path

if TYPE_CHECKING:
    from collections.abc import Iterator


def _is_integration_enabled() -> bool:
    flag = os.getenv("RUN_WORKLINE_INTEGRATION", "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _candidate_database_urls() -> list[str]:
    urls: list[str] = []
    override = os.getenv("INTEGRATION_DATABASE_URL")
    if override:
        urls.append(override)

    urls.append(str(settings.DATABASE_URL))
    for url in list(urls):
        if "@db:" in url:
            urls.append(url.replace("@db:", "@localhost:"))

    # de-duplicate while preserving order
    return list(dict.fromkeys(urls))


def _candidate_redis_urls() -> list[str]:
    urls: list[str] = []
    override = os.getenv("INTEGRATION_REDIS_URL")
    if override:
        urls.append(override)

    urls.append(str(settings.REDIS_URL))
    for url in list(urls):
        urls.append(url.replace("@db:", "@localhost:"))
        urls.append(url.replace("@redis:", "@localhost:"))

    return list(dict.fromkeys(urls))


@pytest.fixture(scope="session")
def integration_guard() -> None:
    if not _is_integration_enabled():
        pytest.skip("integration tests disabled. set RUN_WORKLINE_INTEGRATION=1 to enable")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def integration_engine(integration_guard: None) -> AsyncEngine:
    last_error: Exception | None = None
    for database_url in _candidate_database_urls():
        kwargs: dict[str, object] = {
            "echo": False,
            "pool_pre_ping": True,
            "poolclass": NullPool,
        }
        if not database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"server_settings": {"search_path": get_schema_search_path()}}

        engine = create_async_engine(database_url, **kwargs)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return engine
        except Exception as exc:  # pragma: no cover - only exercised when environment is down
            last_error = exc
            await engine.dispose()

    pytest.skip(f"no reachable postgres for integration tests: {last_error}")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_client(integration_guard: None) -> Redis:
    last_error: Exception | None = None
    for redis_url in _candidate_redis_urls():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.ping()
            return client
        except Exception as exc:  # pragma: no cover - only exercised when environment is down
            last_error = exc
            await client.close()

    pytest.skip(f"no reachable redis for integration tests: {last_error}")


@pytest.fixture(scope="function")
def patch_global_session_factory(integration_engine: AsyncEngine) -> Iterator[None]:
    import src.database.db as db_module

    old_engine = db_module.engine
    old_session_local = db_module.AsyncSessionLocal
    db_module.engine = integration_engine
    db_module.AsyncSessionLocal = async_sessionmaker(
        integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    try:
        yield
    finally:
        db_module.engine = old_engine
        db_module.AsyncSessionLocal = old_session_local


@pytest_asyncio.fixture(scope="function")
async def integration_session_factory(
    integration_engine: AsyncEngine,
    patch_global_session_factory: None,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(integration_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function")
async def integration_db_session(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncSession:
    async with integration_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture(scope="function")
def eager_celery(patch_global_session_factory: None) -> Iterator[None]:
    from src.celery_app.app import celery_app
    from src.celery_app.tasks import workline as _

    old_always_eager = bool(celery_app.conf.task_always_eager)
    old_eager_propagates = bool(celery_app.conf.task_eager_propagates)
    old_ignore_result = bool(celery_app.conf.task_ignore_result)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_ignore_result = False
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = old_always_eager
        celery_app.conf.task_eager_propagates = old_eager_propagates
        celery_app.conf.task_ignore_result = old_ignore_result


@pytest.fixture(scope="function")
def inline_task_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.celery_app.tasks.workline as workline_tasks

    def _run_async_inline(coro: object) -> object:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)  # pragma: no cover - only for sync tests
        return loop.create_task(coro)

    monkeypatch.setattr(workline_tasks, "_run_async", _run_async_inline)


@pytest.fixture(scope="session")
def celery_worker_process(integration_guard: None) -> Iterator[dict[str, str]]:
    from src.celery_app.app import celery_app

    celery_app.loader.import_default_modules()

    repo_root = Path(__file__).resolve().parents[2]
    hostname = f"it-worker-{uuid.uuid4().hex[:8]}@localhost"
    queue_name = f"it-celery-{uuid.uuid4().hex[:8]}"
    with tempfile.NamedTemporaryFile(
        mode="w+",
        prefix="wes-celery-worker-",
        suffix=".log",
        delete=False,
    ) as log_file:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_root}:{existing_pythonpath}" if existing_pythonpath else str(repo_root)

        command = [
            "uv",
            "run",
            "celery",
            "-A",
            "src.celery_app.app",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=INFO",
            "--queues",
            queue_name,
            "--hostname",
            hostname,
        ]

        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    log_file.flush()
                    raise RuntimeError(
                        f"Celery worker exited early with code {process.returncode}. log={log_file.name}"
                    )

                try:
                    ping_result = celery_app.control.inspect(
                        destination=[hostname],
                        timeout=1,
                    ).ping()
                except Exception:
                    ping_result = None

                if ping_result and hostname in ping_result:
                    yield {
                        "hostname": hostname,
                        "queue": queue_name,
                        "log_path": log_file.name,
                    }
                    break

                time.sleep(1)
            else:
                raise RuntimeError(f"Celery worker did not become ready in time. log={log_file.name}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest_asyncio.fixture(scope="function")
async def test_prefix(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> str:
    prefix = f"it_{uuid.uuid4().hex[:12]}"
    yield prefix

    async with integration_session_factory() as cleanup_session:
        prefixed_worklines = select(WorkLine.id).where(WorkLine.line_code.like(f"{prefix}%"))  # type: ignore[arg-type]

        await cleanup_session.execute(
            delete(WorklineInbox).where(  # type: ignore[arg-type]
                or_(
                    WorklineInbox.trace_id.like(f"{prefix}%"),
                    WorklineInbox.workline_id.in_(prefixed_worklines),
                )
            )
        )
        await cleanup_session.execute(
            delete(SystemOutbox).where(  # type: ignore[arg-type]
                or_(
                    SystemOutbox.dispatch_key.like(f"{prefix}%"),
                    SystemOutbox.workline_id.in_(prefixed_worklines),
                    SystemOutbox.session_id.in_(
                        select(WorklineSession.id).where(
                            or_(
                                WorklineSession.trace_id.like(f"{prefix}%"),
                                WorklineSession.workline_id.in_(prefixed_worklines),
                            )
                        )
                    ),
                )
            )
        )
        await cleanup_session.execute(
            delete(WorklineTimeline).where(  # type: ignore[arg-type]
                or_(
                    WorklineTimeline.workline_id.in_(prefixed_worklines),
                    WorklineTimeline.session_id.in_(
                        select(WorklineSession.id).where(
                            or_(
                                WorklineSession.trace_id.like(f"{prefix}%"),
                                WorklineSession.workline_id.in_(prefixed_worklines),
                            )
                        )
                    ),
                )
            )
        )
        await cleanup_session.execute(
            delete(WorklineSession).where(  # type: ignore[arg-type]
                or_(
                    WorklineSession.trace_id.like(f"{prefix}%"),
                    WorklineSession.workline_id.in_(prefixed_worklines),
                )
            )
        )
        await cleanup_session.execute(
            delete(DeviceCommand).where(  # type: ignore[arg-type]
                or_(
                    DeviceCommand.trace_id.like(f"{prefix}%"),
                    DeviceCommand.device_id.in_(
                        select(Device.id).where(
                            or_(
                                Device.device_code.like(f"{prefix}%"),
                                Device.work_line_id.in_(prefixed_worklines),
                            )
                        )
                    ),
                )
            )
        )
        await cleanup_session.execute(
            delete(Device).where(  # type: ignore[arg-type]
                or_(
                    Device.device_code.like(f"{prefix}%"),
                    Device.work_line_id.in_(prefixed_worklines),
                )
            )
        )
        await cleanup_session.execute(
            delete(WorkLine).where(WorkLine.line_code.like(f"{prefix}%"))  # type: ignore[arg-type]
        )
        await cleanup_session.commit()
