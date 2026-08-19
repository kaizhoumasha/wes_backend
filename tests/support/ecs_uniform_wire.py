"""DeviceCommand 生产接线 HEAVY 测试的统一 ECS fake 与真实 broker worker。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

import psutil
import uvicorn
from celery import Celery  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from redis import Redis
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.device.v1.ecs_callback import router as ecs_callback_router
from src.celery_app.app import celery_app

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICE_COMMAND_QUEUE = "device-command"
BROKER_KEY_PREFIX_ENV = "DEVICE_COMMAND_BROKER_KEY_PREFIX"
DEVICE_COMMAND_STARTUP_PROBE_TASK = "tests.support.ecs_uniform_wire.device_command_startup_probe"
DEVICE_COMMAND_STARTUP_PROBE_TOKEN = "device-command-startup-accepted"


def _broker_transport_options(key_prefix: str) -> dict[str, object]:
    return {"global_keyprefix": key_prefix}


@celery_app.task(name=DEVICE_COMMAND_STARTUP_PROBE_TASK)
def device_command_startup_probe() -> dict[str, int | str]:
    return {"token": DEVICE_COMMAND_STARTUP_PROBE_TOKEN, "pid": os.getpid()}


# 独立 worker 通过 --include 导入本支撑模块，在建立 broker 连接前绑定隔离前缀。
if key_prefix := os.getenv(BROKER_KEY_PREFIX_ENV):
    celery_app.conf.broker_transport_options = _broker_transport_options(key_prefix)
    celery_app.conf.result_backend_transport_options = _broker_transport_options(key_prefix)


class _UniformEcsHandler(BaseHTTPRequestHandler):
    server: UniformEcsServer

    def do_GET(self) -> None:
        if not self.path.startswith("/api/v1/device/status?"):
            self.send_error(404)
            return
        device_code = self.path.split("device_code=", 1)[-1]
        self.server.status_requests.append(device_code)
        self._send_json(
            200,
            {
                "device_code": device_code,
                "contract_key": "arm.pick",
                "contract_version": "2.0",
                "mode": "AUTO",
                "status": "IDLE",
                "current_command_code": None,
                "error_detail": None,
                "timestamp": int(time.time() * 1000),
            },
            no_store=True,
        )

    def do_POST(self) -> None:
        if self.path != "/api/v1/device/command":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        command = json.loads(self.rfile.read(length))
        self.server.command_requests.append(command)
        callback_payload = {
            "command_code": command["command_code"],
            "device_code": command["device_code"],
            "contract_key": command["contract_key"],
            "contract_version": command["contract_version"],
            "result": "SUCCESS",
            "finish_time": int(time.time() * 1000),
            "source_event_id": f"RESULT-{hashlib.sha256(command['command_code'].encode()).hexdigest()}",
            "data": {"physical_result": "DONE"},
            "error_detail": None,
        }
        if command.get("trace_id") is not None:
            callback_payload["trace_id"] = command["trace_id"]
        callback_request = urllib_request.Request(  # noqa: S310 - callback URL 由本地测试服务构造。
            self.server.callback_url,
            data=json.dumps(callback_payload, separators=(",", ":")).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(callback_request, timeout=5) as response:  # noqa: S310 - localhost test server。
                self.server.callback_responses.append({"status": response.status, "body": json.loads(response.read())})
        except BaseException as error:
            self.server.callback_errors.append(error)
            self._send_json(500, {"code": 500, "message": "CALLBACK_FAILED"})
            return
        ack = {"code": 200, "message": "ACCEPTED"}
        if command.get("trace_id") is not None:
            ack["trace_id"] = command["trace_id"]
        self._send_json(200, ack)

    def _send_json(self, status: int, payload: dict[str, Any], *, no_store: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        if no_store:
            self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


class UniformEcsServer(ThreadingHTTPServer):
    """只实现批准的 status/command wire，并把结果送回固定 WES callback。"""

    daemon_threads = True

    def __init__(self, *, callback_url: str) -> None:
        super().__init__(("127.0.0.1", 0), _UniformEcsHandler)
        self.callback_url = callback_url
        self.status_requests: list[str] = []
        self.command_requests: list[dict[str, Any]] = []
        self.callback_responses: list[dict[str, Any]] = []
        self.callback_errors: list[BaseException] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> UniformEcsServer:
        self._thread.start()
        return self

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=2)


class WesCallbackServer:
    """在真实 TCP 端口运行生产 ECS callback handler。"""

    def __init__(self, *, session_factory: Any) -> None:
        app = FastAPI()
        app.state.device_evidence_service = DeviceEvidenceService(session_factory=session_factory)
        app.include_router(ecs_callback_router, prefix="/api/v1/callback")
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(128)
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._socket]},
            daemon=True,
        )

    @property
    def result_url(self) -> str:
        return f"http://127.0.0.1:{self._socket.getsockname()[1]}/api/v1/callback/result"

    def start(self) -> WesCallbackServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() >= deadline:
                raise AssertionError("WES callback server failed to start")
            time.sleep(0.01)
        return self

    def close(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
        self._socket.close()
        if self._thread.is_alive():
            raise AssertionError("WES callback server failed to stop")


def _worker_environment(
    *,
    database_url: str,
    redis_url: str,
    provider_file: Path,
    run_id: str,
    key_prefix: str,
) -> dict[str, str]:
    database = make_url(database_url)
    redis = make_url(redis_url)
    return {
        **os.environ,
        "APP_ENV": "test",
        "POSTGRES_HOST": str(database.host),
        "POSTGRES_PORT": str(database.port or 5432),
        "POSTGRES_USER": str(database.username or ""),
        "POSTGRES_PASSWORD": str(database.password or ""),
        "POSTGRES_DB": str(database.database or ""),
        "DATABASE_RUNTIME_ROLE": "integration",
        "DATABASE_POOL_SIZE": "1",
        "DATABASE_MAX_OVERFLOW": "0",
        "DATABASE_APPLICATION_NAME": "it-device-command",
        "DATABASE_APPLICATION_RUN_ID": run_id,
        "REDIS_HOST": str(redis.host),
        "REDIS_PORT": str(redis.port or 6379),
        "REDIS_PASSWORD": str(redis.password or ""),
        "REDIS_DB": str((redis.database or "0").lstrip("/")),
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
        "CELERY_WORKER_QUEUES": DEVICE_COMMAND_QUEUE,
        "CELERY_WORKER_CONCURRENCY": "1",
        "WMS_PROVIDER_PROCESS_ROLE": "wes",
        "WMS_PROVIDER_PROFILE_FILE": str(provider_file),
        "ECS_CONNECT_TIMEOUT_SECONDS": "2",
        "ECS_READ_TIMEOUT_SECONDS": "3",
        "DEVICE_COMMAND_QUEUE": DEVICE_COMMAND_QUEUE,
        BROKER_KEY_PREFIX_ENV: key_prefix,
        "PYTHONPATH": f"{REPO_ROOT}:{os.environ.get('PYTHONPATH', '')}".rstrip(":"),
    }


@dataclass
class DeviceCommandBrokerWorker:
    """连接显式 test PostgreSQL/Redis 的真实单并发 WES worker。"""

    database_url: str
    redis_url: str
    provider_file: Path
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    process: subprocess.Popen[str] | None = field(default=None, init=False)
    log_path: Path | None = field(default=None, init=False)
    key_prefix: str = field(init=False)
    producer: Celery = field(init=False, repr=False)
    _log_file: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        redis = make_url(self.redis_url)
        redis_db = int((redis.database or "0").lstrip("/"))
        if redis.host not in {"127.0.0.1", "localhost", "::1"} or redis_db <= 0:
            raise AssertionError("DeviceCommand broker harness requires local non-zero Redis DB")
        self.key_prefix = f"it:device-command:{self.run_id}:"
        self.producer = Celery(
            f"it-device-command-producer-{self.run_id}",
            broker=self.redis_url,
            backend=self.redis_url,
        )
        from src.celery_app.config import task_routes

        self.producer.conf.task_routes = task_routes
        self.producer.conf.broker_transport_options = _broker_transport_options(self.key_prefix)
        self.producer.conf.result_backend_transport_options = _broker_transport_options(self.key_prefix)

    def start(self) -> DeviceCommandBrokerWorker:
        self._log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - 生命周期跨越 start/close。
            mode="w+",
            prefix=f"wes-device-command-{self.run_id}-",
            suffix=".log",
            delete=False,
        )
        self.log_path = Path(self._log_file.name)
        self.process = subprocess.Popen(
            [
                shutil.which("uv") or "uv",
                "run",
                "celery",
                "-A",
                "src.celery_app.app",
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "--loglevel=INFO",
                f"--queues={DEVICE_COMMAND_QUEUE}",
                f"--hostname=it-device-command-{self.run_id}@localhost",
                "--without-gossip",
                "--without-mingle",
                "--include",
                "tests.support.ecs_uniform_wire",
            ],
            cwd=REPO_ROOT,
            env=_worker_environment(
                database_url=self.database_url,
                redis_url=self.redis_url,
                provider_file=self.provider_file,
                run_id=self.run_id,
                key_prefix=self.key_prefix,
            ),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self._wait_for_startup_probe(time.monotonic() + 30)
        return self

    def _wait_for_startup_probe(self, deadline: float) -> None:
        assert self.process is not None and self.log_path is not None and self._log_file is not None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f"DeviceCommand worker exited early; log={self.log_path}")
            self._log_file.flush()
            log_text = self.log_path.read_text(errors="replace")
            if " ready." in log_text:
                result = self.producer.send_task(
                    DEVICE_COMMAND_STARTUP_PROBE_TASK, kwargs={}, queue=DEVICE_COMMAND_QUEUE
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                response = result.get(timeout=remaining, disable_sync_subtasks=False)
                if (
                    not isinstance(response, dict)
                    or response.get("token") != DEVICE_COMMAND_STARTUP_PROBE_TOKEN
                    or not isinstance(response.get("pid"), int)
                    or response["pid"] <= 0
                ):
                    raise AssertionError(f"DeviceCommand worker startup probe rejected: {response!r}")
                return
            time.sleep(0.1)
        raise AssertionError(f"DeviceCommand worker readiness timed out; log={self.log_path}")

    def run_task(self, task_name: str, *, timeout: float = 30) -> Any:
        result = self.producer.send_task(task_name, kwargs={"limit": 100}, queue=DEVICE_COMMAND_QUEUE)
        return result.get(timeout=timeout, disable_sync_subtasks=False)

    def close(self, *, success: bool) -> None:
        errors: list[BaseException] = []
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self.process is not None:
            try:
                descendants = psutil.Process(self.process.pid).children(recursive=True)
                _, alive = psutil.wait_procs(descendants, timeout=5)
                if alive:
                    errors.append(AssertionError(f"DeviceCommand worker descendants survived: {alive}"))
            except psutil.NoSuchProcess:
                pass
        try:
            self.producer.close()
            redis = Redis.from_url(self.redis_url, decode_responses=True)
            try:
                keys = list(redis.scan_iter(match=f"{self.key_prefix}*"))
                if keys:
                    redis.delete(*keys)
            finally:
                redis.close()
        except BaseException as error:
            errors.append(error)
        if self._log_file is not None:
            self._log_file.close()
        if success and not errors and self.log_path is not None:
            self.log_path.unlink(missing_ok=True)
        if errors:
            raise BaseExceptionGroup("DeviceCommand worker cleanup failed", errors)


__all__ = ["DeviceCommandBrokerWorker", "UniformEcsServer", "WesCallbackServer"]
