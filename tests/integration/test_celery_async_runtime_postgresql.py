"""Celery prefork 单异步运行时的真实 PostgreSQL/Redis 回归。

本文件是显式运行的 heavy integration test。每个 Worker 使用唯一 queue、hostname、
run-id 与 Redis global key prefix，并且只连接临时 PostgreSQL 数据库。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import asyncpg
import psutil
import pytest
from celery import Celery  # pyright: ignore[reportMissingTypeStubs]
from celery.signals import (  # pyright: ignore[reportMissingTypeStubs]
    task_prerun,
    task_received,
    worker_before_create_process,
    worker_process_init,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url

from redis import Redis
from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async
from src.core.logger import logger
from src.database.db import get_db_context
from src.database.redis_client import is_redis_available
from tests.contracts.wms_integration.provider_profile_support import write_provider_profile
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from celery.result import AsyncResult  # pyright: ignore[reportMissingTypeStubs]

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_READY_TIMEOUT = 30.0
TASK_TIMEOUT = 30.0
CONNECTION_DRAIN_TIMEOUT = 15.0
VISIBILITY_TIMEOUT = 4
PROBE_TASK = "tests.integration.celery_prefork.runtime_probe"
IDEMPOTENT_RETRY_TASK = "tests.integration.celery_prefork.idempotent_retry"
TRANSACTION_TASK = "tests.integration.celery_prefork.transaction_probe"
FAMILY_TASKS = (
    "src.celery_app.tasks.core.health_check",
    "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
    "src.celery_app.tasks.workline.scan_timeouts_batch",
    "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
    "src.celery_app.tasks.handling.process_signal",
)


def _write_probe_marker(kind: str, **payload: object) -> None:
    probe_log = os.getenv("PREFORK_PROBE_LOG")
    if not probe_log:
        return
    record = {"kind": kind, "timestamp": time.time(), **payload}
    descriptor = os.open(probe_log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, f"PREFORK_MARKER {json.dumps(record, sort_keys=True)}\n".encode())
    finally:
        os.close(descriptor)


@worker_before_create_process.connect
def _record_child_fork_start(**_: object) -> None:
    _write_probe_marker("child_fork_start", parent_pid=os.getpid())


@worker_process_init.connect
def _record_child_init_complete(**_: object) -> None:
    _write_probe_marker(
        "child_init_complete",
        pid=os.getpid(),
        redis_available=is_redis_available(),
        state="ready" if is_redis_available() else "degraded",
    )


@task_prerun.connect
def _record_task_pid(task_id: str | None = None, task: Any = None, **_: object) -> None:
    task_name = str(getattr(task, "name", ""))
    if task_name:
        _write_probe_marker("task_prerun", task_id=task_id, task_name=task_name, pid=os.getpid())


@task_received.connect
def _record_task_received(request: Any = None, **_: object) -> None:
    if request is not None:
        delivery = dict(getattr(request, "delivery_info", {}) or {})
        _write_probe_marker(
            "task_received",
            task_id=str(getattr(request, "id", "")),
            task_name=str(getattr(request, "name", "")),
            redelivered=bool(delivery.get("redelivered")),
        )


def _transport_options() -> dict[str, object]:
    prefix = os.getenv("PREFORK_REDIS_KEY_PREFIX", "")
    return {
        "global_keyprefix": prefix,
        "visibility_timeout": VISIBILITY_TIMEOUT,
        "socket_timeout": 2,
        "socket_connect_timeout": 2,
    }


# `--include` 让独立 Worker 导入本测试模块；生产者也使用相同 transport 配置。
if os.getenv("PREFORK_REDIS_KEY_PREFIX"):
    celery_app.conf.broker_transport_options = _transport_options()
    celery_app.conf.result_backend_transport_options = _transport_options()


@celery_app.task(name=PROBE_TASK)
def runtime_probe(label: str) -> dict[str, object]:
    """返回 child 的真实 runtime/engine/连接身份。"""

    async def _probe() -> dict[str, object]:
        import src.database.db as db_module
        from src.core.conf import settings

        transport_runtime = celery_async_runtime.transport_runtime
        assert transport_runtime is not None
        async with get_db_context() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT pg_backend_pid(), current_database(), current_setting('application_name'), "
                        "inet_server_addr()::text, inet_server_port()"
                    )
                )
            ).one()
        return {
            "label": label,
            "pid": os.getpid(),
            "runner_id": celery_async_runtime.runner_generation,
            "engine_id": db_module._engine_generation,
            "session_factory_id": db_module._session_factory_generation,
            "transport_client_id": id(transport_runtime.client),
            "backend_pid": int(row[0]),
            "database": str(row[1]),
            "application_name": str(row[2]),
            "server_host": str(row[3]),
            "server_port": int(row[4]),
            "configured_host": str(settings.POSTGRES_HOST),
            "configured_port": int(settings.POSTGRES_PORT),
            "role": str(db_module._engine_owner_role),
            "redis_available": is_redis_available(),
        }

    result = run_async(_probe)
    logger.info(f"PREFORK_RUNTIME_PROBE {json.dumps(result, sort_keys=True)}")
    _write_probe_marker("runtime_probe", **result)
    return result


@celery_app.task(bind=True, name=IDEMPOTENT_RETRY_TASK, max_retries=1)
def idempotent_retry(self: Any, operation_key: str, countdown: int = 5) -> dict[str, object]:
    """以唯一键记录 at-least-once 重投，首次执行后进入 countdown retry。"""

    async def _record(final: bool) -> int:
        async with get_db_context() as db:
            await db.execute(
                text(
                    "INSERT INTO wes_runtime.celery_prefork_acceptance(operation_key, attempts, completed) "
                    "VALUES (:key, 1, :completed) "
                    "ON CONFLICT (operation_key) DO UPDATE "
                    "SET attempts = wes_runtime.celery_prefork_acceptance.attempts + 1, "
                    "completed = wes_runtime.celery_prefork_acceptance.completed OR EXCLUDED.completed"
                ),
                {"key": operation_key, "completed": final},
            )
            await db.commit()
            attempts = await db.scalar(
                text("SELECT attempts FROM wes_runtime.celery_prefork_acceptance WHERE operation_key = :key"),
                {"key": operation_key},
            )
            return int(attempts or 0)

    retries = int(self.request.retries or 0)
    attempts = run_async(lambda: _record(retries > 0))
    if retries == 0:
        raise self.retry(countdown=countdown)
    return {
        "operation_key": operation_key,
        "attempts": attempts,
        "completed": True,
        "pid": os.getpid(),
        "redelivered": bool((self.request.delivery_info or {}).get("redelivered")),
    }


@celery_app.task(name=TRANSACTION_TASK)
def transaction_probe(operation_key: str, hold_seconds: float = 2.0) -> dict[str, object]:
    """持有真实事务，供 TERM warm shutdown 验证。"""

    async def _transaction() -> dict[str, object]:
        # 先提交 started 证据，测试进程据此确定 TERM 发生在任务执行期间。
        async with get_db_context() as db:
            await db.execute(
                text(
                    "INSERT INTO wes_runtime.celery_prefork_acceptance(operation_key, attempts, completed) "
                    "VALUES (:key, 1, FALSE) ON CONFLICT (operation_key) DO NOTHING"
                ),
                {"key": operation_key},
            )
            await db.commit()
        async with get_db_context() as db:
            await db.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": hold_seconds})
            await db.execute(
                text("UPDATE wes_runtime.celery_prefork_acceptance SET completed = TRUE WHERE operation_key = :key"),
                {"key": operation_key},
            )
            await db.commit()
        return {"operation_key": operation_key, "completed": True, "pid": os.getpid()}

    return run_async(_transaction)


def _required_integration_urls() -> tuple[str, str]:
    if os.getenv("RUN_WORKLINE_INTEGRATION", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("integration tests disabled. set RUN_WORKLINE_INTEGRATION=1 to enable")
    database_url = os.getenv("INTEGRATION_DATABASE_URL", "").strip()
    redis_url = os.getenv("INTEGRATION_REDIS_URL", "").strip()
    if not database_url or not redis_url:
        pytest.skip("prefork integration requires explicit INTEGRATION_DATABASE_URL and INTEGRATION_REDIS_URL")
    return database_url, redis_url


def _component_environment(database_url: str, redis_url: str, *, run_id: str) -> dict[str, str]:
    database = make_url(database_url)
    redis = make_url(redis_url)
    if database.get_backend_name() != "postgresql" or not database.database:
        raise AssertionError("INTEGRATION_DATABASE_URL must target PostgreSQL")
    if redis.get_backend_name() != "redis":
        raise AssertionError("INTEGRATION_REDIS_URL must target Redis")
    return {
        "POSTGRES_HOST": str(database.host),
        "POSTGRES_PORT": str(database.port or 5432),
        "POSTGRES_USER": str(database.username or ""),
        "POSTGRES_PASSWORD": str(database.password or ""),
        "POSTGRES_DB": database.database,
        "DATABASE_RUNTIME_ROLE": "integration",
        "DATABASE_POOL_SIZE": "1",
        "DATABASE_MAX_OVERFLOW": "0",
        "DATABASE_APPLICATION_NAME": "it",
        "DATABASE_APPLICATION_RUN_ID": run_id,
        "REDIS_HOST": str(redis.host),
        "REDIS_PORT": str(redis.port or 6379),
        "REDIS_PASSWORD": str(redis.password or ""),
        "REDIS_DB": str((redis.database or "0").lstrip("/")),
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
    }


@pytest.fixture(scope="module")
def prefork_services(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """为模块创建一个迁移到 head 的隔离数据库。"""

    admin_database_url, redis_url = _required_integration_urls()
    runner = asyncio.Runner()
    database_context = temporary_database(
        environ={**os.environ, "INTEGRATION_DATABASE_URL": admin_database_url},
        required_free_slots=8,
    )
    provider_profile = write_provider_profile(tmp_path_factory.mktemp("wms-provider") / "provider.yaml")
    database, sqlalchemy_url = runner.run(database_context.__aenter__())
    try:
        run_alembic("upgrade", "head", database_url=sqlalchemy_url)
        connection_url = make_url(sqlalchemy_url).set(drivername="postgresql")

        async def _create_acceptance_table() -> None:
            connection = await asyncpg.connect(connection_url.render_as_string(hide_password=False))
            try:
                await connection.execute(
                    "CREATE TABLE wes_runtime.celery_prefork_acceptance ("
                    "operation_key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, completed BOOLEAN NOT NULL)"
                )
            finally:
                await connection.close()

        runner.run(_create_acceptance_table())
        yield {
            "database": database,
            "database_url": sqlalchemy_url,
            "redis_url": redis_url,
            "wms_provider_profile_file": str(provider_profile),
        }
    finally:
        runner.run(database_context.__aexit__(None, None, None))
        runner.close()


def _wait_until(predicate: Any, timeout: float, description: str) -> Any:
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while time.monotonic() < deadline:
        last_value = predicate()
        if last_value:
            return last_value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}; last={last_value!r}")


def _probe_markers(log_text: str, kind: str | None = None) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in log_text.splitlines():
        marker, separator, raw_record = line.partition("PREFORK_MARKER ")
        if not separator or marker:
            continue
        record = cast("dict[str, object]", json.loads(raw_record))
        if kind is None or record.get("kind") == kind:
            records.append(record)
    return records


def _worker_connections(database_url: str, run_id: str) -> list[dict[str, object]]:
    url = make_url(database_url).set(drivername="postgresql")

    async def _query() -> list[dict[str, object]]:
        observer_name = f"it:cli:observer:{os.getpid()}:{run_id}-observer"
        connection = await asyncpg.connect(
            url.render_as_string(hide_password=False),
            server_settings={"application_name": observer_name[:63]},
        )
        try:
            observer_pid = connection.get_server_pid()
            rows = await connection.fetch(
                "SELECT pid, application_name, state FROM pg_stat_activity "
                "WHERE application_name LIKE $1 AND pid <> $2 ORDER BY pid",
                f"%{run_id}%",
                observer_pid,
            )
            return [dict(row) for row in rows if not str(row["application_name"]).endswith("-observer")]
        finally:
            await connection.close()

    return asyncio.run(_query())


def _acceptance_row(database_url: str, operation_key: str) -> dict[str, object] | None:
    url = make_url(database_url).set(drivername="postgresql")

    async def _query() -> dict[str, object] | None:
        connection = await asyncpg.connect(url.render_as_string(hide_password=False))
        try:
            row = await connection.fetchrow(
                "SELECT operation_key, attempts, completed FROM wes_runtime.celery_prefork_acceptance "
                "WHERE operation_key = $1",
                operation_key,
            )
            return dict(row) if row is not None else None
        finally:
            await connection.close()

    return asyncio.run(_query())


class _HangingRedisHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        # 接收连接和命令但永不回应，真实触发 redis-py ping 的取消/候选池清理路径。
        self.request.recv(4096)
        server = cast("_HangingRedisServer", self.server)
        server.first_command_at = time.monotonic()
        self.request.settimeout(0.05)
        while time.monotonic() - server.first_command_at < 10:
            try:
                if not self.request.recv(4096):
                    server.connection_closed_at = time.monotonic()
                    return
            except TimeoutError:  # noqa: S112 - 黑洞端点通过轮询等待客户端关闭
                continue


class _HangingRedisServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    first_command_at: float | None = None
    connection_closed_at: float | None = None


@contextmanager
def _hanging_redis_url() -> Iterator[tuple[str, _HangingRedisServer]]:
    server = _HangingRedisServer(("127.0.0.1", 0), _HangingRedisHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"redis://127.0.0.1:{server.server_address[1]}/0", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@dataclass
class PreforkWorker:
    services: Mapping[str, str]
    concurrency: int = 1
    max_tasks_per_child: int = 1000
    application_redis_url: str | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    process: subprocess.Popen[str] | None = field(default=None, init=False)
    log_path: Path | None = field(default=None, init=False)
    project_log_dir: Path | None = field(default=None, init=False)
    hostname: str = field(init=False)
    queue: str = field(init=False)
    key_prefix: str = field(init=False)
    _log_file: Any = field(default=None, init=False, repr=False)
    _descendant_pids: set[int] = field(default_factory=set, init=False)
    _producer_app: Celery | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.hostname = f"it-{self.run_id}@localhost"
        self.queue = f"it-prefork-{self.run_id}"
        self.key_prefix = f"it:{self.run_id}:"

    def start(self) -> PreforkWorker:
        environment = os.environ.copy()
        environment.update(
            _component_environment(self.services["database_url"], self.services["redis_url"], run_id=self.run_id)
        )
        environment["WMS_PROVIDER_PROFILE_FILE"] = self.services["wms_provider_profile_file"]
        if self.application_redis_url is not None:
            app_redis = make_url(self.application_redis_url)
            environment.update(
                REDIS_HOST=str(app_redis.host),
                REDIS_PORT=str(app_redis.port or 6379),
                REDIS_PASSWORD=str(app_redis.password or ""),
                REDIS_DB=str((app_redis.database or "0").lstrip("/")),
            )
        environment["PREFORK_REDIS_KEY_PREFIX"] = self.key_prefix
        environment["PYTHONPATH"] = f"{REPO_ROOT}:{environment.get('PYTHONPATH', '')}".rstrip(":")
        environment["WORKLINE_ALLOW_NULL_PLUGIN"] = "1"
        command = [
            "uv",
            "run",
            "celery",
            "-A",
            "src.celery_app.app",
            "worker",
            "--pool=prefork",
            f"--concurrency={self.concurrency}",
            f"--max-tasks-per-child={self.max_tasks_per_child}",
            "--loglevel=INFO",
            "--queues",
            self.queue,
            "--hostname",
            self.hostname,
            "--include",
            "tests.integration.test_celery_async_runtime_postgresql",
            "--without-gossip",
            "--without-mingle",
        ]
        try:
            self._log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - worker 生命周期跨越 start/stop
                mode="w+", prefix=f"wes-prefork-{self.run_id}-", suffix=".log", delete=False
            )
            self.log_path = Path(self._log_file.name)
            self._log_file.close()
            self._log_file = self.log_path.open("a+")
            self.project_log_dir = Path(tempfile.mkdtemp(prefix=f"wes-prefork-project-{self.run_id}-"))
            environment["PREFORK_PROBE_LOG"] = str(self.log_path)
            environment["LOG_DIR"] = str(self.project_log_dir)
            environment["LOG_DISABLE_FILE"] = "false"
            self.process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                env=environment,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            _wait_until(self._is_ready, WORKER_READY_TIMEOUT, f"worker {self.hostname} readiness")
            ready_probe = cast("dict[str, object]", self.result(self.submit(PROBE_TASK, "worker-ready")))
            expected_database = make_url(self.services["database_url"])
            expected_target = {
                "database": self.services["database"],
                "configured_host": str(expected_database.host),
                "configured_port": int(expected_database.port or 5432),
                "role": "integration",
            }
            actual_target = {key: ready_probe[key] for key in expected_target}
            if actual_target != expected_target:
                raise AssertionError(f"worker connected to unexpected target: {ready_probe}")
            if not ready_probe["server_host"] or int(ready_probe["server_port"]) != int(expected_database.port or 5432):
                raise AssertionError(f"worker PostgreSQL server endpoint mismatch: {ready_probe}")
            if self.run_id not in str(ready_probe["application_name"]):
                raise AssertionError(f"worker application run-id missing: {ready_probe}")
            self._capture_descendants()
            return self
        except BaseException as start_error:
            if self.process is None:
                cleanup_errors: list[BaseException] = []
                try:
                    if self._log_file is not None:
                        self._log_file.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                try:
                    if self.log_path is not None:
                        self.log_path.unlink(missing_ok=True)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                try:
                    if self.project_log_dir is not None:
                        shutil.rmtree(self.project_log_dir)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                if cleanup_errors:
                    raise BaseExceptionGroup(
                        f"worker {self.run_id} Popen 前失败且临时资源清理失败",
                        [start_error, *cleanup_errors],
                    ) from start_error
                raise
            try:
                self.stop()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    f"worker {self.run_id} start 与清理均失败；日志保留于 {self.log_path}",
                    [start_error, cleanup_error],
                ) from start_error
            raise

    def _is_ready(self) -> bool:
        assert self.process is not None and self.log_path is not None
        if self.process.poll() is not None:
            raise AssertionError(f"worker exited code={self.process.returncode}; log={self.log_path}")
        self._log_file.flush()
        return " ready." in self.log_path.read_text(errors="replace")

    def _capture_descendants(self) -> None:
        assert self.process is not None
        try:
            self._descendant_pids.update(
                child.pid for child in psutil.Process(self.process.pid).children(recursive=True)
            )
        except psutil.Error:
            pass

    def configure_producer(self) -> Celery:
        if self._producer_app is not None:
            return self._producer_app
        options = {"global_keyprefix": self.key_prefix, "visibility_timeout": VISIBILITY_TIMEOUT}
        self._producer_app = Celery(
            f"prefork-producer-{self.run_id}",
            broker=self.services["redis_url"],
            backend=self.services["redis_url"],
        )
        self._producer_app.conf.broker_transport_options = options
        self._producer_app.conf.result_backend_transport_options = options
        return self._producer_app

    def submit(self, task_name: str, *args: object, countdown: int | None = None) -> AsyncResult:
        producer = self.configure_producer()
        options: dict[str, object] = {"queue": self.queue}
        if countdown is not None:
            options["countdown"] = countdown
        return cast("AsyncResult", producer.send_task(task_name, args=list(args), **options))

    def result(self, result: AsyncResult, timeout: float = TASK_TIMEOUT) -> Any:
        return result.get(timeout=timeout, disable_sync_subtasks=False)

    def log_text(self) -> str:
        assert self.log_path is not None
        self._log_file.flush()
        return self.log_path.read_text(errors="replace")

    def project_log_text(self) -> str:
        assert self.project_log_dir is not None
        app_log = self.project_log_dir / "app.log"
        return app_log.read_text(errors="replace") if app_log.exists() else ""

    def stop(
        self,
        *,
        shutdown_signal: signal.Signals = signal.SIGTERM,
        success: bool = False,
        cleanup_redis: bool = True,
    ) -> None:
        if self.process is None:
            return
        process = self.process
        errors: list[BaseException] = []
        try:
            self._capture_descendants()
        except BaseException as exc:
            errors.append(exc)
        if process.poll() is None:
            try:
                os.kill(process.pid, shutdown_signal)
                process.wait(timeout=20)
            except subprocess.TimeoutExpired as exc:
                errors.append(exc)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except BaseException as kill_error:
                    errors.append(kill_error)
            except BaseException as exc:
                errors.append(exc)
        try:
            self._capture_descendants()
        except BaseException as exc:
            errors.append(exc)

        alive: list[psutil.Process] = []
        try:
            descendants = [psutil.Process(pid) for pid in self._descendant_pids if psutil.pid_exists(pid)]
            _, alive = psutil.wait_procs(descendants, timeout=5)
        except BaseException as exc:
            errors.append(exc)
        if alive:
            # leader 即使已退出，descendants 仍留在 start_new_session 创建的原 PGID；
            # 对 PGID 兜底 KILL 后继续 Redis/DB/log 清理，最终再聚合报告错误。
            try:
                os.killpg(process.pid, signal.SIGKILL)
                _, alive = psutil.wait_procs(alive, timeout=5)
            except BaseException as exc:
                errors.append(exc)
            if alive:
                errors.append(
                    AssertionError(f"worker descendants survived PGID KILL: {[child.pid for child in alive]}")
                )
        if cleanup_redis:
            try:
                self.cleanup_run_artifacts()
            except BaseException as exc:
                errors.append(exc)
        else:
            try:
                self._wait_for_connection_drain()
            except BaseException as exc:
                errors.append(exc)
        try:
            if self._producer_app is not None:
                self._producer_app.close()
        except BaseException as exc:
            errors.append(exc)
        try:
            self._log_file.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            self.process = None
        if success and not errors and self.log_path is not None:
            self.log_path.unlink(missing_ok=True)
            if self.project_log_dir is not None:
                shutil.rmtree(self.project_log_dir)
        if errors:
            raise BaseExceptionGroup(
                f"worker {self.run_id} teardown 有 {len(errors)} 项失败；日志保留于 {self.log_path}", errors
            )

    def _cleanup_redis(self) -> None:
        client = Redis.from_url(self.services["redis_url"], decode_responses=True)
        try:
            keys = list(client.scan_iter(match=f"{self.key_prefix}*"))
            keys.extend(key for key in (self.queue, f"_kombu.binding.{self.queue}") if client.exists(key))
            if keys:
                client.delete(*set(keys))
            assert not list(client.scan_iter(match=f"{self.key_prefix}*"))
            assert not client.exists(self.queue)
        finally:
            client.close()

    def _wait_for_connection_drain(self) -> None:
        _wait_until(
            lambda: not _worker_connections(self.services["database_url"], self.run_id),
            CONNECTION_DRAIN_TIMEOUT,
            f"worker {self.run_id} PostgreSQL connections drain",
        )

    def cleanup_run_artifacts(self) -> None:
        """无论 replacement 是否启动成功，都清理该 run 的 transport 与连接。"""
        errors: list[BaseException] = []
        for cleanup in (self._cleanup_redis, self._wait_for_connection_drain):
            try:
                cleanup()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup(f"worker {self.run_id} run artifacts 清理失败", errors)


def _assert_clean_activity(worker: PreforkWorker, application_names: set[str]) -> None:
    rows = _worker_connections(worker.services["database_url"], worker.run_id)
    assert rows
    assert {str(row["application_name"]) for row in rows} == application_names
    assert all(row["state"] != "idle in transaction" for row in rows)
    forbidden = (
        "attached to a different loop",
        "another operation is in progress",
        "protocol",
        "terminating connection",
    )
    log_text = worker.log_text().lower()
    assert not any(message in log_text for message in forbidden)


@contextmanager
def _quit_scenario(first: PreforkWorker) -> Iterator[dict[str, object]]:
    """统一管理 QUIT 全场景，任何失败都由 first 显式回收整个 run。"""
    state: dict[str, object] = {"replacement": None, "success": False}
    body_error: BaseException | None = None
    body_traceback = None
    try:
        yield state
    except BaseException as exc:
        body_error = exc
        body_traceback = exc.__traceback__
    finally:
        errors: list[BaseException] = []
        replacement = cast("PreforkWorker | None", state["replacement"])
        success = bool(state["success"])
        fallback_to_first = not success
        if success:
            if replacement is None:
                errors.append(AssertionError("QUIT replacement 未接管却标记成功"))
                fallback_to_first = True
            else:
                try:
                    replacement.stop(success=True)
                except BaseException as exc:
                    errors.append(exc)
                    fallback_to_first = True
        if fallback_to_first:
            if replacement is not None:
                try:
                    replacement.stop(cleanup_redis=False)
                except BaseException as exc:
                    errors.append(exc)
            try:
                first.stop(cleanup_redis=False)
            except BaseException as exc:
                errors.append(exc)
            try:
                first.cleanup_run_artifacts()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            if body_error is not None:
                errors.insert(0, body_error)
            raise BaseExceptionGroup("QUIT 场景外层清理失败", errors)
    if body_error is not None:
        raise body_error.with_traceback(body_traceback)


def test_prefork_concurrency_two_owns_one_runtime_and_engine_per_child(prefork_services: dict[str, str]) -> None:
    worker = PreforkWorker(prefork_services, concurrency=2).start()
    success = False
    try:
        probes = [worker.submit(PROBE_TASK, f"probe-{index}") for index in range(12)]
        probe_rows = [cast("dict[str, object]", worker.result(result)) for result in probes]
        by_pid: dict[int, list[dict[str, object]]] = {}
        for row in probe_rows:
            by_pid.setdefault(int(row["pid"]), []).append(row)
        assert len(by_pid) == 2
        assert all(len({str(row["runner_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert all(len({str(row["engine_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert all(len({str(row["session_factory_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert all(len({int(row["transport_client_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert {str(row["database"]) for row in probe_rows} == {prefork_services["database"]}
        assert {str(row["role"]) for row in probe_rows} == {"integration"}
        application_names = {str(row["application_name"]) for row in probe_rows}
        assert len(application_names) == 2
        assert all(worker.run_id in name and ":integration:" in name for name in application_names)

        family_tasks = list(
            zip(
                FAMILY_TASKS,
                ([], [0], [0], [0], [{"run_id": worker.run_id}]),
                strict=True,
            )
        )
        family_results = [worker.submit(name, *args) for name, args in family_tasks]
        for result in family_results:
            worker.result(result)
        family_task_ids = {str(result.id) for result in family_results}
        family_markers = _wait_until(
            lambda: (
                [
                    marker
                    for marker in _probe_markers(worker.log_text(), "task_prerun")
                    if str(marker["task_id"]) in family_task_ids
                ]
                if family_task_ids
                <= {str(marker["task_id"]) for marker in _probe_markers(worker.log_text(), "task_prerun")}
                else []
            ),
            TASK_TIMEOUT,
            "five task families PID evidence",
        )
        marker_by_id = {str(marker["task_id"]): marker for marker in family_markers}
        family_pids = [int(marker_by_id[str(result.id)]["pid"]) for result in family_results]
        assert set(family_pids) == set(by_pid)
        assert {str(marker_by_id[str(result.id)]["task_name"]) for result in family_results} == set(FAMILY_TASKS)
        _assert_clean_activity(worker, application_names)
        assert len(_probe_markers(worker.log_text(), "runtime_probe")) >= len(probe_rows)
        success = True
    finally:
        worker.stop(success=success)


def test_max_tasks_per_child_rebuilds_runtime_engine_and_application_name(prefork_services: dict[str, str]) -> None:
    worker = PreforkWorker(prefork_services, concurrency=1, max_tasks_per_child=1).start()
    success = False
    try:
        first = cast("dict[str, object]", worker.result(worker.submit(PROBE_TASK, "first")))
        second = cast("dict[str, object]", worker.result(worker.submit(PROBE_TASK, "second")))
        assert int(first["pid"]) != int(second["pid"])
        assert str(first["runner_id"]) != str(second["runner_id"])
        assert str(first["engine_id"]) != str(second["engine_id"])
        assert str(first["session_factory_id"]) != str(second["session_factory_id"])
        assert str(first["application_name"]) != str(second["application_name"])
        assert int(first["backend_pid"]) != int(second["backend_pid"])
        _wait_until(
            lambda: all(
                int(row["pid"]) != int(first["backend_pid"])
                for row in _worker_connections(prefork_services["database_url"], worker.run_id)
            ),
            CONNECTION_DRAIN_TIMEOUT,
            "recycled child PostgreSQL connection drain",
        )
        _assert_clean_activity(worker, {str(second["application_name"])})
        success = True
    finally:
        worker.stop(success=success)


def test_term_warm_shutdown_finishes_transaction_and_releases_connections(prefork_services: dict[str, str]) -> None:
    worker = PreforkWorker(prefork_services, concurrency=1).start()
    success = False
    operation_key = f"term-{worker.run_id}"
    try:
        result = worker.submit(TRANSACTION_TASK, operation_key, 2.0)

        def _transaction_started() -> dict[str, object] | None:
            row = _acceptance_row(prefork_services["database_url"], operation_key)
            if row is None and result.ready():
                worker.result(result)
            return row

        _wait_until(
            _transaction_started,
            TASK_TIMEOUT,
            "TERM transaction started evidence",
        )
        assert worker.process is not None
        started = time.monotonic()
        os.kill(worker.process.pid, signal.SIGTERM)
        final = cast("dict[str, object]", worker.result(result))
        worker.process.wait(timeout=20)
        elapsed = time.monotonic() - started
        row = _acceptance_row(prefork_services["database_url"], operation_key)
        assert final["completed"] is True
        assert row is not None and row["completed"] is True
        assert elapsed < 20
        assert "Warm shutdown" in worker.log_text()
        success = True
    finally:
        worker.stop(success=success)


def test_quit_countdown_retry_is_recovered_with_idempotent_final_state(prefork_services: dict[str, str]) -> None:
    first = PreforkWorker(prefork_services, concurrency=1).start()
    operation_key = f"quit-{first.run_id}"
    first_log_path = first.log_path
    retry_countdown = 30
    result = first.submit(IDEMPOTENT_RETRY_TASK, operation_key, retry_countdown)
    with _quit_scenario(first) as quit_state:
        _wait_until(
            lambda: (
                (row := _acceptance_row(prefork_services["database_url"], operation_key))
                and int(row["attempts"]) >= 1
                and row["completed"] is False
            ),
            TASK_TIMEOUT,
            "countdown retry first attempt",
        )
        first_attempt_seen = time.monotonic()
        shutdown_started = time.monotonic()
        first.stop(shutdown_signal=signal.SIGQUIT, cleanup_redis=False)
        shutdown_elapsed = time.monotonic() - shutdown_started
        assert first_log_path is not None
        first_log = first_log_path.read_text(errors="replace")
        assert 9 <= shutdown_elapsed < 20
        assert "Soft Shutdown" in first_log and "10 seconds" in first_log and "Cold shutdown" in first_log

        replacement = PreforkWorker(first.services, concurrency=1, run_id=first.run_id)
        quit_state["replacement"] = replacement
        replacement.start()
        redelivery_wait_budget = min(
            TASK_TIMEOUT,
            max(0.0, retry_countdown - (time.monotonic() - first_attempt_seen)),
        )
        handoff_marker = _wait_until(
            lambda: next(
                (
                    marker
                    for marker in _probe_markers(replacement.log_text(), "task_received")
                    if marker["task_id"] == result.id
                ),
                None,
            ),
            redelivery_wait_budget,
            "replacement Worker countdown retry handoff",
        )
        handoff_received_elapsed = time.monotonic() - first_attempt_seen
        final = cast("dict[str, object]", replacement.result(result, timeout=TASK_TIMEOUT + VISIBILITY_TIMEOUT))
        row = _acceptance_row(prefork_services["database_url"], operation_key)
        assert final["completed"] is True
        # 首 Worker 可能已预取 retry 后恢复，也可能在预取前退出；两条 broker 路径的
        # redelivered 标记不同，但替代 Worker 必须接管同一 task id 并保持幂等终态。
        assert final["redelivered"] is handoff_marker["redelivered"]
        assert row is not None and row["completed"] is True
        assert int(row["attempts"]) >= 2
        assert handoff_marker["task_name"] == IDEMPOTENT_RETRY_TASK
        assert VISIBILITY_TIMEOUT <= handoff_received_elapsed < retry_countdown
        quit_state["success"] = True
    first_log_path.unlink(missing_ok=True)
    if first.project_log_dir is not None:
        shutil.rmtree(first.project_log_dir)


def test_hanging_application_redis_degrades_within_budget_and_db_task_survives(
    prefork_services: dict[str, str],
) -> None:
    with _hanging_redis_url() as (hanging_url, hanging_server):
        worker = PreforkWorker(prefork_services, concurrency=1, application_redis_url=hanging_url)
        success = False
        started = time.monotonic()
        worker.start()
        try:
            probe = cast("dict[str, object]", worker.result(worker.submit(PROBE_TASK, "redis-degraded")))
            elapsed = time.monotonic() - started
            markers = _probe_markers(worker.log_text())
            init_marker = next(marker for marker in markers if marker["kind"] == "child_init_complete")
            assert probe["redis_available"] is False
            assert probe["database"] == prefork_services["database"]
            assert hanging_server.first_command_at is not None and hanging_server.connection_closed_at is not None
            assert 0.8 <= hanging_server.connection_closed_at - hanging_server.first_command_at <= 3.25
            assert init_marker["state"] == "degraded"
            assert "Worker Redis 初始化超时，进入降级模式" in worker.project_log_text()
            assert elapsed < 8  # 还包含 MainProcess 启动和 ready probe，不作为 3 秒预算证据。
            success = True
        finally:
            worker.stop(success=success)
