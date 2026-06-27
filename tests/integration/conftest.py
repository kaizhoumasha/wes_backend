from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# 预加载外键目标模型，避免 SQLModel 在 flush 时出现 NoReferencedTableError。
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.sys.models import SystemOutbox
from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.app.workline.models.inbox import InboxStatus, WorklineInbox
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.models.timeline import WorklineTimeline
from src.app.workline.models.workline import WorkLine
from src.core.conf import settings
from src.database.schema_conf import get_schema_search_path
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Iterator


def _is_integration_enabled() -> bool:
    return _truthy_env("RUN_WORKLINE_INTEGRATION")


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _database_url_label(database_url: str) -> str:
    parsed = urlparse(database_url)
    database_name = parsed.path.lstrip("/") or "<unknown-db>"
    return f"{parsed.hostname or '<unknown-host>'}/{database_name}"


def _is_safe_integration_database_url(database_url: str) -> bool:
    parsed = urlparse(database_url)
    hostname = (parsed.hostname or "").lower()
    database_name = parsed.path.lstrip("/").lower()
    is_local_host = hostname in {"localhost", "127.0.0.1", "::1", "db"}
    is_test_database = database_name in {"test"} or database_name.startswith("test_") or database_name.endswith("_test")
    if _truthy_env("ALLOW_SHARED_DEV_DB_INTEGRATION"):
        return is_local_host
    return is_local_host and is_test_database


def _candidate_database_urls() -> list[str]:
    override = os.getenv("INTEGRATION_DATABASE_URL")
    if not override:
        raise RuntimeError(
            "Workline integration tests require INTEGRATION_DATABASE_URL. "
            "Point it at the docker/local test database. For a local shared dev database, "
            "also set ALLOW_SHARED_DEV_DB_INTEGRATION=1."
        )
    urls: list[str] = [override]
    for url in list(urls):
        if "@db:" in url:
            urls.append(url.replace("@db:", "@localhost:"))

    # de-duplicate while preserving order
    safe_urls = list(dict.fromkeys(urls))
    unsafe_urls = [url for url in safe_urls if not _is_safe_integration_database_url(url)]
    if unsafe_urls:
        labels = ", ".join(_database_url_label(url) for url in unsafe_urls)
        raise RuntimeError(
            "Workline integration tests require a Docker/local/test database URL. "
            f"Unsafe candidates: {labels}. "
            "Set INTEGRATION_DATABASE_URL to the local docker test database. For a local shared dev database, "
            "also set ALLOW_SHARED_DEV_DB_INTEGRATION=1."
        )
    return safe_urls


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

    pytest.fail(f"no reachable postgres for integration tests: {last_error}")


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

    pytest.fail(f"no reachable redis for integration tests: {last_error}")


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


async def _find_hot_queue_inboxes(db: AsyncSession) -> list[tuple[object, ...]]:
    result = await db.execute(
        select(
            WorklineInbox.id,
            WorklineInbox.status,
            WorklineInbox.next_retry_at,
            WorklineInbox.updated_at,
        )
        .where(WorklineInbox.status.in_([InboxStatus.NEW, InboxStatus.RETRY, InboxStatus.PROCESSING]))  # type: ignore[arg-type]
        .order_by(WorklineInbox.received_at.asc(), WorklineInbox.id.asc())  # type: ignore[arg-type]
        .limit(5)
    )
    return list(result.all())


async def _count_timed_out_sessions(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(WorklineSession)
        .outerjoin(DeviceCommand, WorklineSession.awaiting_device_command_code == DeviceCommand.command_code)
        .where(  # type: ignore[arg-type]
            WorklineSession.deadline_at.isnot(None),
            WorklineSession.deadline_at < timezone.now_for_db(),
            or_(
                and_(
                    WorklineSession.status == SessionStatus.WAITING_DEVICE_RESULT,
                    WorklineSession.awaiting_device_command_code.isnot(None),
                    DeviceCommand.status == CommandStatus.ACK_RECEIVED,
                    DeviceCommand.ack_received_at.isnot(None),
                ),
                WorklineSession.status == SessionStatus.WAITING_EXTERNAL,
            ),
        )
    )
    return int(result.scalar_one() or 0)


async def _count_ack_timed_out_commands(db: AsyncSession) -> int:
    result = await db.execute(
        select(DeviceCommand).where(  # type: ignore[arg-type]
            DeviceCommand.status == CommandStatus.SENT,
            DeviceCommand.sent_at.is_not(None),
            DeviceCommand.ack_received_at.is_(None),
            DeviceCommand.correlation_id.is_not(None),
            DeviceCommand.workline_id.is_not(None),
        )
    )
    return sum(1 for command in result.scalars().all() if command.is_timeout())


@pytest_asyncio.fixture(scope="function")
async def isolated_workline_inbox_queue(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_session_factory() as db:
        hot_queue_rows = await _find_hot_queue_inboxes(db)
    if hot_queue_rows:
        pytest.fail(f"WorkLine inbox 全局 task smoke 需要空队列；当前 Docker DB 仍有热队列 inbox: {hot_queue_rows}")


@pytest_asyncio.fixture(scope="function")
async def isolated_workline_timeout_queue(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_session_factory() as db:
        timed_out_session_count = await _count_timed_out_sessions(db)
        ack_timeout_count = await _count_ack_timed_out_commands(db)
    if timed_out_session_count or ack_timeout_count:
        pytest.fail(
            "WorkLine timeout 全局 task smoke 需要空队列；"
            f"当前 Docker DB 仍有 {timed_out_session_count} 个超时 session、"
            f"{ack_timeout_count} 条 ACK 超时 command。"
        )


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
        keep_log = True
        log_path = Path(log_file.name)
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_root}:{existing_pythonpath}" if existing_pythonpath else str(repo_root)
        env["WORKLINE_ALLOW_NULL_PLUGIN"] = "1"

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
                    keep_log = False
                    try:
                        yield {
                            "hostname": hostname,
                            "queue": queue_name,
                            "log_path": log_file.name,
                        }
                    except BaseException:
                        keep_log = True
                        raise
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
            if not keep_log:
                log_path.unlink(missing_ok=True)


@pytest_asyncio.fixture(scope="function")
async def test_prefix(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> str:
    prefix = f"it_{uuid.uuid4().hex[:12]}"
    yield prefix

    async with integration_session_factory() as cleanup_session:
        prefixed_worklines = select(WorkLine.id).where(WorkLine.line_code.like(f"{prefix}%"))  # type: ignore[arg-type]
        prefixed_sessions = select(WorklineSession.id).where(
            or_(
                WorklineSession.trace_id.like(f"{prefix}%"),  # type: ignore[arg-type]
                WorklineSession.workline_id.in_(prefixed_worklines),
            )
        )
        prefixed_inboxes = select(WorklineInbox.id).where(
            or_(
                WorklineInbox.trace_id.like(f"{prefix}%"),  # type: ignore[arg-type]
                WorklineInbox.workline_id.in_(prefixed_worklines),
            )
        )
        prefixed_outboxes = select(SystemOutbox.id).where(
            or_(
                SystemOutbox.dispatch_key.like(f"{prefix}%"),  # type: ignore[arg-type]
                SystemOutbox.workline_id.in_(prefixed_worklines),
                SystemOutbox.session_id.in_(prefixed_sessions),
            )
        )

        await cleanup_session.execute(
            delete(WorklineDiagnostic).where(  # type: ignore[arg-type]
                or_(
                    WorklineDiagnostic.trace_id.like(f"{prefix}%"),
                    WorklineDiagnostic.request_id.like(f"{prefix}%"),
                    WorklineDiagnostic.device_code.like(f"{prefix}%"),
                    WorklineDiagnostic.workline_id.in_(prefixed_worklines),
                    WorklineDiagnostic.session_id.in_(prefixed_sessions),
                    WorklineDiagnostic.inbox_id.in_(prefixed_inboxes),
                    WorklineDiagnostic.outbox_id.in_(prefixed_outboxes),
                )
            )
        )
        await cleanup_session.execute(
            delete(SystemOutbox).where(  # type: ignore[arg-type]
                or_(
                    SystemOutbox.dispatch_key.like(f"{prefix}%"),
                    SystemOutbox.workline_id.in_(prefixed_worklines),
                    SystemOutbox.session_id.in_(prefixed_sessions),
                )
            )
        )
        await cleanup_session.execute(
            delete(WorklineInbox).where(  # type: ignore[arg-type]
                or_(
                    WorklineInbox.trace_id.like(f"{prefix}%"),
                    WorklineInbox.workline_id.in_(prefixed_worklines),
                )
            )
        )
        await cleanup_session.execute(
            delete(WorklineTimeline).where(  # type: ignore[arg-type]
                or_(
                    WorklineTimeline.workline_id.in_(prefixed_worklines),
                    WorklineTimeline.session_id.in_(prefixed_sessions),
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
