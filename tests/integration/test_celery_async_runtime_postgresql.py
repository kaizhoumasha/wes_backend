"""Celery prefork 单异步运行时的真实 PostgreSQL/Redis 回归。

本文件是显式运行的 heavy integration test。每个 Worker 使用唯一 queue、hostname、
run-id 与 Redis global key prefix，并且只连接临时 PostgreSQL 数据库。
"""

from __future__ import annotations

import asyncio
import json
import os
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
from sqlalchemy import text
from sqlalchemy.engine import make_url

from redis import Redis
from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async
from src.core.logger import logger
from src.database.db import get_db_context
from src.database.redis_client import is_redis_available
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

        async with get_db_context() as db:
            row = (
                await db.execute(
                    text("SELECT pg_backend_pid(), current_database(), current_setting('application_name')")
                )
            ).one()
        return {
            "label": label,
            "pid": os.getpid(),
            "runner_id": id(celery_async_runtime._runner),
            "engine_id": id(db_module.engine),
            "session_factory_id": id(db_module.AsyncSessionLocal),
            "backend_pid": int(row[0]),
            "database": str(row[1]),
            "application_name": str(row[2]),
            "role": str(db_module._engine_owner_role),
            "redis_available": is_redis_available(),
        }

    result = run_async(_probe)
    logger.info(f"PREFORK_RUNTIME_PROBE {json.dumps(result, sort_keys=True)}")
    # Celery child 的项目 logger 使用独立 sink；测试专用汇总日志让 harness
    # 能从一个稳定文件确认两个 child 的 runtime/engine 身份。
    probe_log = os.getenv("PREFORK_PROBE_LOG")
    if probe_log:
        with Path(probe_log).open("a", encoding="utf-8") as stream:
            stream.write(f"PREFORK_RUNTIME_PROBE {json.dumps(result, sort_keys=True)}\n")
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
    return {"operation_key": operation_key, "attempts": attempts, "completed": True, "pid": os.getpid()}


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
def prefork_services() -> Iterator[dict[str, str]]:
    """为模块创建一个迁移到 head 的隔离数据库。"""

    admin_database_url, redis_url = _required_integration_urls()
    runner = asyncio.Runner()
    database_context = temporary_database(
        environ={**os.environ, "INTEGRATION_DATABASE_URL": admin_database_url},
        required_free_slots=8,
    )
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
        threading.Event().wait(30)


class _HangingRedisServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@contextmanager
def _hanging_redis_url() -> Iterator[str]:
    server = _HangingRedisServer(("127.0.0.1", 0), _HangingRedisHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"redis://127.0.0.1:{server.server_address[1]}/0"
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
        self._log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - worker 生命周期跨越 start/stop
            mode="w+", prefix=f"wes-prefork-{self.run_id}-", suffix=".log", delete=False
        )
        self.log_path = Path(self._log_file.name)
        environment["PREFORK_PROBE_LOG"] = str(self.log_path)
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
        if ready_probe["database"] != self.services["database"] or ready_probe["role"] != "integration":
            raise AssertionError(f"worker connected to unexpected target: {ready_probe}")
        self._capture_descendants()
        return self

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

    def stop(
        self,
        *,
        shutdown_signal: signal.Signals = signal.SIGTERM,
        success: bool = False,
        cleanup_redis: bool = True,
    ) -> None:
        if self.process is None:
            return
        self._capture_descendants()
        if self.process.poll() is None:
            os.kill(self.process.pid, shutdown_signal)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self._capture_descendants()
        _, alive = psutil.wait_procs(
            [psutil.Process(pid) for pid in self._descendant_pids if psutil.pid_exists(pid)], timeout=5
        )
        if alive:
            raise AssertionError(f"worker descendants survived teardown: {[process.pid for process in alive]}")
        if cleanup_redis:
            self._cleanup_redis()
        _wait_until(
            lambda: not _worker_connections(self.services["database_url"], self.run_id),
            CONNECTION_DRAIN_TIMEOUT,
            f"worker {self.run_id} PostgreSQL connections drain",
        )
        self._log_file.close()
        if success and self.log_path is not None:
            self.log_path.unlink(missing_ok=True)
        self.process = None

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


def _assert_clean_activity(worker: PreforkWorker, application_names: set[str]) -> None:
    rows = _worker_connections(worker.services["database_url"], worker.run_id)
    assert rows
    assert {str(row["application_name"]) for row in rows} <= application_names
    assert all(row["state"] != "idle in transaction" for row in rows)
    forbidden = (
        "attached to a different loop",
        "another operation is in progress",
        "protocol",
        "terminating connection",
    )
    log_text = worker.log_text().lower()
    assert not any(message in log_text for message in forbidden)


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
        assert all(len({int(row["runner_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert all(len({int(row["engine_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert all(len({int(row["session_factory_id"]) for row in rows}) == 1 for rows in by_pid.values())
        assert {str(row["database"]) for row in probe_rows} == {prefork_services["database"]}
        assert {str(row["role"]) for row in probe_rows} == {"integration"}
        application_names = {str(row["application_name"]) for row in probe_rows}
        assert len(application_names) == 2
        assert all(worker.run_id in name and ":integration:" in name for name in application_names)

        family_tasks = [
            ("src.celery_app.tasks.core.health_check", []),
            ("src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch", [0]),
            ("src.celery_app.tasks.workline.scan_timeouts_batch", [0]),
            ("src.celery_app.tasks.sys.dispatch_system_outbox_batch", [0]),
            ("src.celery_app.tasks.handling.process_signal", [{"run_id": worker.run_id}]),
        ]
        family_results = [worker.submit(name, *args) for name, args in family_tasks]
        for result in family_results:
            worker.result(result)
        _assert_clean_activity(worker, application_names)
        assert worker.log_text().count("PREFORK_RUNTIME_PROBE") >= len(probe_rows)
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
        assert str(first["application_name"]) != str(second["application_name"])
        assert (int(first["pid"]), int(first["runner_id"]), int(first["engine_id"])) != (
            int(second["pid"]),
            int(second["runner_id"]),
            int(second["engine_id"]),
        )
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


def test_quit_countdown_retry_is_redelivered_with_idempotent_final_state(prefork_services: dict[str, str]) -> None:
    first = PreforkWorker(prefork_services, concurrency=1).start()
    operation_key = f"quit-{first.run_id}"
    first_log_path = first.log_path
    result = first.submit(IDEMPOTENT_RETRY_TASK, operation_key, 5)
    try:
        _wait_until(
            lambda: (
                (row := _acceptance_row(prefork_services["database_url"], operation_key))
                and int(row["attempts"]) >= 1
                and row["completed"] is False
            ),
            TASK_TIMEOUT,
            "countdown retry first attempt",
        )
        first.stop(shutdown_signal=signal.SIGQUIT, cleanup_redis=False)
    except BaseException:
        first.stop(cleanup_redis=False)
        raise

    assert first_log_path is not None
    first_log = first_log_path.read_text(errors="replace")
    assert "Cold shutdown" in first_log or "Soft Shutdown" in first_log

    replacement = PreforkWorker(prefork_services, concurrency=1, run_id=first.run_id).start()
    success = False
    try:
        final = cast("dict[str, object]", replacement.result(result, timeout=TASK_TIMEOUT + VISIBILITY_TIMEOUT))
        row = _acceptance_row(prefork_services["database_url"], operation_key)
        assert final["completed"] is True
        assert row is not None and row["completed"] is True
        assert int(row["attempts"]) >= 2
        success = True
    finally:
        replacement.stop(success=success)
        if success:
            first_log_path.unlink(missing_ok=True)


def test_hanging_application_redis_degrades_within_budget_and_db_task_survives(
    prefork_services: dict[str, str],
) -> None:
    with _hanging_redis_url() as hanging_url:
        worker = PreforkWorker(prefork_services, concurrency=1, application_redis_url=hanging_url)
        success = False
        started = time.monotonic()
        worker.start()
        try:
            probe = cast("dict[str, object]", worker.result(worker.submit(PROBE_TASK, "redis-degraded")))
            elapsed = time.monotonic() - started
            assert probe["redis_available"] is False
            assert probe["database"] == prefork_services["database"]
            # 常规 Worker 启动约 2 秒；Redis ping+cleanup 仍必须被 3 秒共享预算封顶。
            assert elapsed < 8
            assert '"redis_available": false' in worker.log_text().lower()
            success = True
        finally:
            worker.stop(success=success)
