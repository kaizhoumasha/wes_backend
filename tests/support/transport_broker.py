"""Transport 生产接线 heavy test 的真实 broker、worker 与 mock WMS 支撑。"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncpg
import psutil
from celery import Celery  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy.engine import make_url

from redis import Redis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
# 新鲜 CI 容器需冷导入完整 Celery 应用；业务任务超时仍由 TASK_TIMEOUT_SECONDS 单独约束。
WORKER_READY_TIMEOUT_SECONDS = 60.0
TASK_TIMEOUT_SECONDS = 30.0
CONNECTION_DRAIN_TIMEOUT_SECONDS = 15.0
FULFILLMENT_QUEUE = "wms-fulfillment"
TRANSPORT_BROKER_KEY_PREFIX_ENV = "TRANSPORT_BROKER_KEY_PREFIX"


def _broker_transport_options(key_prefix: str) -> dict[str, object]:
    return {"global_keyprefix": key_prefix}


# 独立 worker 通过 --include 导入本测试支撑；只在显式 test env 下隔离 broker/result key。
if key_prefix := os.getenv(TRANSPORT_BROKER_KEY_PREFIX_ENV):
    from src.celery_app.app import celery_app

    celery_app.conf.broker_transport_options = _broker_transport_options(key_prefix)
    celery_app.conf.result_backend_transport_options = _broker_transport_options(key_prefix)


class _WmsHandler(BaseHTTPRequestHandler):
    server: MockWmsHttpServer

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        envelope = json.loads(self.rfile.read(length))
        with self.server.condition:
            self.server.requests.append({"path": self.path, "envelope": envelope, "received_at": time.monotonic()})
            delay = self.server.delays.popleft() if self.server.delays else 0.0
            self.server.condition.notify_all()
        if delay:
            time.sleep(delay)
        data = envelope["data"]
        response = json.dumps(
            {
                "operation_id": envelope["operation_id"],
                "code": "RECEIVED",
                "timestamp": int(time.time() * 1000),
                "data": {"transport_task_id": data["transport_task_id"]},
            },
            separators=(",", ":"),
        ).encode()
        try:
            self.send_response(202)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        except BrokenPipeError:
            # 慢响应用于真实触发 10 秒 client timeout，客户端先关闭属于预期。
            return

    def log_message(self, _format: str, *args: object) -> None:
        return


class MockWmsHttpServer(ThreadingHTTPServer):
    """按请求顺序施加延迟并返回固定 Transport ACK。"""

    daemon_threads = True

    def __init__(self, delays: tuple[float, ...] = ()) -> None:
        super().__init__(("127.0.0.1", 0), _WmsHandler)
        self.delays = deque(delays)
        self.requests: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._started = False
        self._closed = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> MockWmsHttpServer:
        self._thread.start()
        self._started = True
        return self

    def wait_for_requests(self, count: int, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"timed out waiting for {count} WMS requests; actual={len(self.requests)}")
                self.condition.wait(timeout=remaining)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            self.shutdown()
        self.server_close()
        if self._started:
            self._thread.join(timeout=2)


def _validate_test_redis_url(redis_url: str) -> None:
    redis = make_url(redis_url)
    try:
        database = int((redis.database or "0").lstrip("/"))
    except ValueError:
        database = 0
    if (
        redis.get_backend_name() != "redis"
        or redis.host not in {"127.0.0.1", "localhost", "::1", "redis"}
        or database <= 0
    ):
        raise AssertionError("Transport broker harness requires a local/build-scoped non-zero test database")


def _worker_environment(
    database_url: str,
    redis_url: str,
    profile_file: Path,
    run_id: str,
    key_prefix: str,
) -> dict[str, str]:
    database = make_url(database_url)
    redis = make_url(redis_url)
    return {
        **os.environ,
        "POSTGRES_HOST": str(database.host),
        "POSTGRES_PORT": str(database.port or 5432),
        "POSTGRES_USER": str(database.username or ""),
        "POSTGRES_PASSWORD": str(database.password or ""),
        "POSTGRES_DB": str(database.database or ""),
        "DATABASE_RUNTIME_ROLE": "integration",
        "DATABASE_POOL_SIZE": "1",
        "DATABASE_MAX_OVERFLOW": "0",
        "DATABASE_APPLICATION_NAME": "it-transport",
        "DATABASE_APPLICATION_RUN_ID": run_id,
        "REDIS_HOST": str(redis.host),
        "REDIS_PORT": str(redis.port or 6379),
        "REDIS_PASSWORD": str(redis.password or ""),
        "REDIS_DB": str((redis.database or "0").lstrip("/")),
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
        "CELERY_WORKER_QUEUES": FULFILLMENT_QUEUE,
        "CELERY_WORKER_CONCURRENCY": "1",
        "WMS_PROVIDER_PROCESS_ROLE": "fulfillment",
        "WMS_PROVIDER_PROFILE_FILE": str(profile_file),
        "WMS_DEPLOYMENT_ROLE": "fulfillment-worker",
        "WORKLINE_ALLOW_NULL_PLUGIN": "1",
        TRANSPORT_BROKER_KEY_PREFIX_ENV: key_prefix,
        "PYTHONPATH": f"{REPO_ROOT}:{os.environ.get('PYTHONPATH', '')}".rstrip(":"),
    }


@dataclass
class TransportBrokerWorker:
    """连接显式 test DB/Redis 的单副本、单并发 fulfillment worker。"""

    database_url: str
    redis_url: str
    profile_file: Path
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    hostname: str = field(init=False)
    process: subprocess.Popen[str] | None = field(default=None, init=False)
    log_path: Path | None = field(default=None, init=False)
    key_prefix: str = field(init=False)
    _log_file: Any = field(default=None, init=False, repr=False)
    _descendant_pids: set[int] = field(default_factory=set, init=False)
    producer: Celery = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_test_redis_url(self.redis_url)
        self.hostname = f"it-transport-{self.run_id}@localhost"
        self.key_prefix = f"it:transport:{self.run_id}:"
        self.producer = Celery(
            f"it-transport-producer-{self.run_id}",
            broker=self.redis_url,
            backend=self.redis_url,
        )
        from src.celery_app.config import task_routes

        self.producer.conf.task_routes = task_routes
        self.producer.conf.broker_transport_options = _broker_transport_options(self.key_prefix)
        self.producer.conf.result_backend_transport_options = _broker_transport_options(self.key_prefix)

    def start(self) -> TransportBrokerWorker:
        self._log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - worker 生命周期跨越 start/close。
            mode="w+", prefix=f"wes-transport-{self.run_id}-", suffix=".log", delete=False
        )
        self.log_path = Path(self._log_file.name)
        command = [
            "uv",
            "run",
            "celery",
            "-A",
            "src.celery_app.app",
            "worker",
            "--pool=prefork",
            "--concurrency=1",
            "--loglevel=INFO",
            f"--queues={FULFILLMENT_QUEUE}",
            f"--hostname={self.hostname}",
            "--without-gossip",
            "--without-mingle",
            "--include",
            "tests.support.transport_broker",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=_worker_environment(
                self.database_url,
                self.redis_url,
                self.profile_file,
                self.run_id,
                self.key_prefix,
            ),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + WORKER_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f"Transport worker exited early; log={self.log_path}")
            self._log_file.flush()
            if " ready." in self.log_path.read_text(errors="replace"):
                self._capture_descendants()
                return self
            time.sleep(0.1)
        raise AssertionError(f"Transport worker readiness timed out; log={self.log_path}")

    def send(
        self,
        task_name: str,
        *,
        kwargs: dict[str, object] | None = None,
        expires: float | None = None,
    ) -> Any:
        return self.producer.send_task(
            task_name,
            kwargs=kwargs or {},
            queue=FULFILLMENT_QUEUE,
            expires=expires,
        )

    @staticmethod
    def result(result: Any, timeout: float = TASK_TIMEOUT_SECONDS) -> Any:
        return result.get(timeout=timeout, disable_sync_subtasks=False)

    def log_text(self) -> str:
        if self.log_path is None:
            return ""
        self._log_file.flush()
        return self.log_path.read_text(errors="replace")

    def _capture_descendants(self) -> None:
        if self.process is None:
            return
        try:
            self._descendant_pids.update(
                child.pid for child in psutil.Process(self.process.pid).children(recursive=True)
            )
        except psutil.Error:
            pass

    def _cleanup_broker_artifacts(self) -> None:
        client = Redis.from_url(self.redis_url, decode_responses=True)
        try:
            keys = list(client.scan_iter(match=f"{self.key_prefix}*"))
            if keys:
                client.delete(*keys)
            remaining = list(client.scan_iter(match=f"{self.key_prefix}*"))
            if remaining:
                raise AssertionError(f"Transport broker keys survived cleanup: {remaining}")
        finally:
            client.close()

    async def _query_worker_connections(self) -> list[dict[str, object]]:
        database = make_url(self.database_url).set(drivername="postgresql")
        observer_name = f"it:transport:observer:{os.getpid()}:{self.run_id}"
        connection = await asyncpg.connect(
            database.render_as_string(hide_password=False),
            server_settings={"application_name": observer_name[:63]},
            timeout=2,
        )
        try:
            observer_pid = connection.get_server_pid()
            rows = await connection.fetch(
                "SELECT pid, application_name, state FROM pg_stat_activity "
                "WHERE application_name LIKE $1 AND pid <> $2 ORDER BY pid",
                f"%{self.run_id}%",
                observer_pid,
            )
            return [dict(row) for row in rows if not str(row["application_name"]).endswith("-observer")]
        finally:
            await connection.close(timeout=2)

    def _wait_for_connection_drain(self) -> None:
        errors: list[BaseException] = []

        def _wait() -> None:
            async def _poll() -> None:
                deadline = asyncio.get_running_loop().time() + CONNECTION_DRAIN_TIMEOUT_SECONDS
                while True:
                    rows = await self._query_worker_connections()
                    if not rows:
                        return
                    if asyncio.get_running_loop().time() >= deadline:
                        raise AssertionError(f"Transport worker database connections survived cleanup: {rows}")
                    await asyncio.sleep(0.1)

            try:
                asyncio.run(_poll())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=_wait, name=f"transport-db-drain-{self.run_id}")
        thread.start()
        thread.join(timeout=CONNECTION_DRAIN_TIMEOUT_SECONDS + 5)
        if thread.is_alive():
            raise AssertionError("Transport worker database connection drain check did not finish")
        if errors:
            raise errors[0]

    def close(self, *, success: bool) -> None:
        process = self.process
        errors: list[BaseException] = []
        if process is not None:
            try:
                self._capture_descendants()
            except BaseException as exc:
                errors.append(exc)
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
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
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    _, alive = psutil.wait_procs(alive, timeout=5)
                except BaseException as exc:
                    errors.append(exc)
                if alive:
                    errors.append(
                        AssertionError(
                            f"Transport worker descendants survived PGID KILL: {[child.pid for child in alive]}"
                        )
                    )

        for cleanup in (self.producer.close, self._cleanup_broker_artifacts, self._wait_for_connection_drain):
            try:
                cleanup()
            except BaseException as exc:
                errors.append(exc)
        try:
            if self._log_file is not None:
                self._log_file.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            self.process = None
        if success and not errors and self.log_path is not None:
            self.log_path.unlink(missing_ok=True)
        if errors:
            raise BaseExceptionGroup(
                f"Transport worker {self.run_id} teardown 有 {len(errors)} 项失败；日志保留于 {self.log_path}",
                errors,
            )


async def close_transport_test_resources(
    *,
    worker: TransportBrokerWorker | Any | None,
    runtime: Any | None,
    server: MockWmsHttpServer | Any | None,
    cleanup_database: Callable[[], Awaitable[None]],
    success: bool,
    primary_error: BaseException | None,
) -> None:
    """按 Transport heavy 场景的固定资源顺序完成并聚合所有清理。"""

    cleanup_errors: list[BaseException] = []
    for cleanup in (
        (lambda: worker.close(success=success)) if worker is not None else None,
        runtime.aclose if runtime is not None else None,
        server.close if server is not None else None,
        cleanup_database,
    ):
        if cleanup is None:
            continue
        try:
            result = cleanup()
            if inspect.isawaitable(result):
                await result
        except BaseException as exc:
            cleanup_errors.append(exc)

    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "Transport heavy scenario 与资源清理均失败",
                [primary_error, *cleanup_errors],
            ) from primary_error
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        raise BaseExceptionGroup("Transport heavy 资源清理失败", cleanup_errors)


__all__ = [
    "FULFILLMENT_QUEUE",
    "MockWmsHttpServer",
    "TransportBrokerWorker",
    "close_transport_test_resources",
]
