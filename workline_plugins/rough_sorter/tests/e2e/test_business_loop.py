"""安装后的 rough-sorter 经真实 WES 组件完成单物料主成功路径。"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPO_ROOT / "workline_plugins/rough_sorter/fixtures"
BACKEND_IMAGE = os.getenv("ROUGH_SORTER_E2E_BACKEND_IMAGE", "wes-backend:phase8-rough-sorter")
TRACE_ID = "TRACE-RS-E2E-001"
API_TIMEOUT_SECONDS = 180.0
ADMIN_USERNAME = "rough-sorter-e2e-admin"
ADMIN_PASSWORD = "RoughSorterE2ePassw0rd!"

DEVICE_CONTRACTS = {
    "RS-E2E-MEASUREMENT": "rough_sorter.measurement_device",
    "RS-E2E-TRANSFER": "rough_sorter.transfer_device",
    "RS-E2E-PLACEMENT": "rough_sorter.placement_device",
}


def _run(*args: str, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        input=input_text,
        capture_output=True,
    )


def _json_request(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("content-type", "application/json")
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise AssertionError(f"HTTP {error.code} from {url}: {error.read().decode(errors='replace')}") from error


def _recent_past_timestamp_ms(now_seconds: float) -> int:
    return math.floor(now_seconds * 1000) - 100


def _measurement_scan() -> dict[str, Any]:
    return {
        "device_code": "RS-E2E-MEASUREMENT",
        "event_type": "SCAN_COMPLETED",
        "timestamp": int(time.time() * 1000),
        "data": {
            "material_trace_id": TRACE_ID,
            "LotCode": "LOT-001",
            "DateCode": "20260818",
            "Qty": "1",
            "ProductNo": "PRODUCT-001",
            "MfrPN": "MFR-001",
            "PONumber": "PO-001",
            "diameter_mm": "12.5",
            "thickness_mm": "1.2",
            "shape_result": "PASS",
            "position": {
                "location_id": "MEASUREMENT-1",
                "location_type": "MEASUREMENT_POSITION",
                "material_trace_id": TRACE_ID,
            },
        },
    }


def _released_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


@dataclass(slots=True)
class _BoundaryState:
    api_url: str = ""
    admission_results: list[str] = field(default_factory=lambda: ["ACCEPT"])
    admission_retry_after_ms: int = 500
    admission_accept_release: threading.Event = field(default_factory=_released_event)
    ecs_callback_release: threading.Event = field(default_factory=_released_event)
    wms_requests: list[dict[str, Any]] = field(default_factory=list)
    ecs_commands: list[dict[str, Any]] = field(default_factory=list)
    callback_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class _JsonHandler(BaseHTTPRequestHandler):
    state: ClassVar[_BoundaryState]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise TypeError("stub request must be a JSON object")
        return payload

    def _write_json(self, status: int, payload: dict[str, Any], *, no_store: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        if no_store:
            self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _WmsStubHandler(_JsonHandler):
    """Only emulate the approved WMS wire; this is not real WMS acceptance."""

    def do_POST(self) -> None:
        request = self._read_json()
        operation = request.get("operation")
        expected_path = (
            "/api/v1/wes/facts" if operation == "inbound.material.placement_report@v1" else "/api/v1/wes/decisions"
        )
        if self.path != expected_path:
            self._write_json(404, {"error": "unexpected path"})
            return
        with self.state.lock:
            self.state.wms_requests.append(request)
            if operation == "inbound.material.admission_decide@v1":
                if not self.state.admission_results:
                    self._write_json(500, {"error": "unexpected admission request"})
                    return
                admission_result = self.state.admission_results.pop(0)
        common = {
            "operation_id": request["operation_id"],
            "timestamp": int(time.time() * 1000),
        }
        if operation == "inbound.material.admission_decide@v1":
            if admission_result == "WAIT":
                response = common | {
                    "code": "DECIDED",
                    "data": {
                        "result": "WAIT",
                        "reason_code": "CELL_PENDING",
                        "retry_after_ms": self.state.admission_retry_after_ms,
                    },
                }
            elif admission_result == "ACCEPT":
                if not self.state.admission_accept_release.wait(timeout=API_TIMEOUT_SECONDS):
                    self._write_json(504, {"error": "timed out waiting to release admission ACCEPT"})
                    return
                response = common | {
                    "code": "DECIDED",
                    "data": {
                        "result": "ACCEPT",
                        "pkg_id": "PKG-RS-E2E-001",
                        "inbound_admission_id": "ADM-RS-E2E-001",
                    },
                }
            else:
                self._write_json(500, {"error": f"unsupported admission result: {admission_result}"})
                return
        elif operation == "inbound.material.target_decide@v1":
            response = common | {
                "code": "DECIDED",
                "data": {
                    "result": "ASSIGNED",
                    "target_assignment_id": "TARGET-RS-E2E-001",
                    "target_position": {
                        "type": "ONE_LAYER_BIN_CELL",
                        "rack_id": "RACK-1",
                        "rack_slot_code": "SLOT-1",
                        "bin_id": "BIN-1",
                        "bin_cell_id": "CELL-1",
                    },
                    "placement_sequence": 1,
                    "expected_height_mm": "2.0",
                },
            }
        elif operation == "inbound.material.placement_report@v1":
            response = common | {"code": "RECORDED", "data": {}}
        else:
            self._write_json(422, common | {"code": "REJECTED", "data": {"reason_code": "UNSUPPORTED_OPERATION"}})
            return
        self._write_json(200, response)


class _EcsStubHandler(_JsonHandler):
    """Only emulate the uniform ECS wire; this is not supplier conformance."""

    def do_GET(self) -> None:
        if not self.path.startswith("/api/v1/device/status?"):
            self._write_json(404, {"error": "unexpected path"})
            return
        device_code = self.path.split("device_code=", 1)[-1]
        contract_key = DEVICE_CONTRACTS.get(device_code)
        if contract_key is None:
            self._write_json(404, {"code": 404, "message": "DEVICE_NOT_FOUND"})
            return
        self._write_json(
            200,
            {
                "device_code": device_code,
                "contract_key": contract_key,
                "contract_version": "1.0",
                "mode": "AUTO",
                "status": "IDLE",
                "current_command_code": None,
                "error_detail": None,
                "timestamp": _recent_past_timestamp_ms(time.time()),
            },
            no_store=True,
        )

    def do_POST(self) -> None:
        if self.path != "/api/v1/device/command":
            self._write_json(404, {"error": "unexpected path"})
            return
        command = self._read_json()
        with self.state.lock:
            self.state.ecs_commands.append(command)
        self._write_json(200, {"code": 200, "message": "Accepted"})
        threading.Thread(target=self._callback_success, args=(command,), daemon=True).start()

    def _callback_success(self, command: dict[str, Any]) -> None:
        if not self.state.ecs_callback_release.wait(timeout=API_TIMEOUT_SECONDS):
            with self.state.lock:
                self.state.callback_errors.append("timed out waiting to release ECS callback")
            return
        time.sleep(0.4)
        callback = {
            "command_code": command["command_code"],
            "device_code": command["device_code"],
            "result": "SUCCESS",
            "finish_time": int(datetime.now(UTC).timestamp() * 1000),
            "data": {
                "material_trace_id": command["params"]["material_trace_id"],
                "actual_position": command["params"]["target"],
            },
            "error_detail": None,
        }
        try:
            response = _json_request(f"{self.state.api_url}/api/v1/callback/result", callback, timeout=10)
            if response.get("code") != 200:
                raise AssertionError(f"unexpected callback ACK: {response}")
        except Exception as error:  # callback thread must surface failure to the main assertion.
            with self.state.lock:
                self.state.callback_errors.append(repr(error))


@contextmanager
def _serve(handler: type[_JsonHandler], state: _BoundaryState):
    handler.state = state
    server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _rough_sorter_configuration() -> dict[str, object]:
    contract = {
        "ecs_version": "ecs-e2e-1",
        "gateway_version": "gateway-e2e-1",
        "device_model": "rough-sorter-e2e",
        "firmware_version": "firmware-e2e-1",
        "status_max_age_ms": 600_000,
        "command_timeout_ms": 30_000,
        "time_source": "ecs-stub",
        "allowed_clock_skew_ms": 1_000,
        "callback_retry_window_ms": 60_000,
        "evidence_retention_days": 30,
    }
    return {
        "rough_sorter": {
            "device_contracts": {
                "MEASUREMENT_DEVICE": dict(contract),
                "TRANSFER_DEVICE": dict(contract),
                "PLACEMENT_DEVICE": dict(contract),
            },
            "position_bindings": {
                "MEASUREMENT_POSITION": "MEASUREMENT-1",
                "PIPELINE_INLET": "INLET-1",
                "PIPELINE_OUTLET": "OUTLET-1",
                "NG_POSITION": "NG-1",
            },
        }
    }


def _render_seed(ecs_port: int) -> str:
    now_ms = int(time.time() * 1000)
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now_ms / 1000))
    return (
        (FIXTURE_ROOT / "business-loop-seed.sql")
        .read_text(encoding="utf-8")
        .replace("__NOW__", now)
        .replace("__NOW_MS__", str(now_ms))
        .replace("__ROUGH_SORTER_CONFIG__", json.dumps(_rough_sorter_configuration(), separators=(",", ":")))
        .replace("__ECS_ENDPOINT__", f"http://ecs-stub:{ecs_port}")
    )


def _start_workline(stack: _DockerStack, api_url: str) -> None:
    login = _json_request(
        f"{api_url}/api/v1/auth/login",
        {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    access_token = login["data"]["access_token"]
    result = _json_request(
        f"{api_url}/api/v1/workline/operations/worklines/9001/start",
        {"request_id": "RS-E2E-START-001"},
        headers={"authorization": f"Bearer {access_token}"},
    )
    assert result["code"] == "1000"
    assert result["data"]["created"] is True
    assert result["data"]["plugin_key"] == "rough_sorter"
    assert stack.query("SELECT count(*) FROM wes_biz.line_run_epochs") == "1"
    assert stack.query("SELECT count(*) FROM wes_biz.line_run_epoch_device_bindings") == "3"
    assert stack.query("SELECT count(*) FROM wes_biz.line_run_epoch_position_bindings") == "4"


class _DockerStack:
    def __init__(self, *, wms_port: int, ecs_port: int) -> None:
        suffix = uuid4().hex[:10]
        self.network = f"rs9-e2e-{suffix}"
        self.db = f"rs9-e2e-db-{suffix}"
        self.redis = f"rs9-e2e-redis-{suffix}"
        self.api = f"rs9-e2e-api-{suffix}"
        self.worker = f"rs9-e2e-worker-{suffix}"
        self.fulfillment = f"rs9-e2e-fulfillment-{suffix}"
        self.names = [self.fulfillment, self.worker, self.api, self.redis, self.db]
        self.api_port = self._free_port()
        self.wms_port = wms_port
        self.ecs_port = ecs_port
        self.image = ""
        self.tempdir = tempfile.TemporaryDirectory(prefix="rough-sorter-e2e-")

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def start(self) -> None:
        self._assert_current_image()
        _run("docker", "network", "create", self.network)
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            self.db,
            "--network",
            self.network,
            "--network-alias",
            "db",
            "-e",
            "POSTGRES_USER=wes",
            "-e",
            "POSTGRES_PASSWORD=rough-sorter-e2e",
            "-e",
            "POSTGRES_DB=wes",
            "postgres:16-alpine",
        )
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            self.redis,
            "--network",
            self.network,
            "--network-alias",
            "redis",
            "redis:8-alpine",
            "redis-server",
            "--requirepass",
            "rough-sorter-e2e",
        )
        self._wait_command(self.db, "pg_isready", "-U", "wes", "-d", "wes")
        profile = Path(self.tempdir.name) / "wms-provider.yaml"
        profile.write_text(
            (FIXTURE_ROOT / "business-loop-provider.yaml")
            .read_text(encoding="utf-8")
            .replace("__WMS_STUB_PORT__", str(self.wms_port)),
            encoding="utf-8",
        )
        common = self._application_args(profile)
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            self.api,
            "--network",
            self.network,
            "--network-alias",
            "api",
            "-p",
            f"127.0.0.1:{self.api_port}:8001",
            *common,
            "-e",
            "WMS_DEPLOYMENT_ROLE=api",
            "-e",
            f"BOOTSTRAP_ADMIN_USERNAME={ADMIN_USERNAME}",
            "-e",
            f"BOOTSTRAP_ADMIN_PASSWORD={ADMIN_PASSWORD}",
            "--entrypoint",
            "sh",
            self.image,
            "-c",
            "alembic upgrade head && python scripts/data/bootstrap_foundation.py "
            "&& exec uvicorn main:app --host 0.0.0.0 --port 8001",
        )
        self._wait_http(f"http://127.0.0.1:{self.api_port}/health")
        self._start_worker(self.worker, "wes", "wes-worker", "default,celery,device-command", "1")
        self._start_worker(self.fulfillment, "fulfillment", "fulfillment-worker", "wms-fulfillment", "1")
        self._wait_log(self.worker, "ready.")
        self._wait_log(self.fulfillment, "ready.")

    def _application_args(self, profile: Path) -> list[str]:
        return [
            "--add-host",
            "wms-stub:host-gateway",
            "--add-host",
            "ecs-stub:host-gateway",
            "--env-file",
            str(REPO_ROOT / ".env.test"),
            "-e",
            "POSTGRES_HOST=db",
            "-e",
            "POSTGRES_PORT=5432",
            "-e",
            "POSTGRES_USER=wes",
            "-e",
            "POSTGRES_PASSWORD=rough-sorter-e2e",
            "-e",
            "POSTGRES_DB=wes",
            "-e",
            "REDIS_HOST=redis",
            "-e",
            "REDIS_PORT=6379",
            "-e",
            "REDIS_PASSWORD=rough-sorter-e2e",
            "-e",
            "CELERY_BROKER_URL=redis://:rough-sorter-e2e@redis:6379/1",
            "-e",
            "CELERY_RESULT_BACKEND=redis://:rough-sorter-e2e@redis:6379/2",
            "-e",
            "WMS_PROVIDER_PROFILE_FILE=/run/rough-sorter/wms-provider.yaml",
            "-e",
            "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=false",
            "-v",
            f"{profile}:/run/rough-sorter/wms-provider.yaml:ro",
        ]

    def _start_worker(self, name: str, process_role: str, deployment_role: str, queues: str, concurrency: str) -> None:
        profile = Path(self.tempdir.name) / "wms-provider.yaml"
        embedded_beat = ["-B"] if process_role == "wes" else []
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            *self._application_args(profile),
            "-e",
            f"WMS_PROVIDER_PROCESS_ROLE={process_role}",
            "-e",
            f"WMS_DEPLOYMENT_ROLE={deployment_role}",
            "-e",
            f"CELERY_WORKER_QUEUES={queues}",
            "-e",
            f"CELERY_WORKER_CONCURRENCY={concurrency}",
            "--entrypoint",
            "celery",
            self.image,
            "-A",
            "src.celery_app.app",
            "worker",
            "--loglevel=INFO",
            "--pool=solo",
            f"--concurrency={concurrency}",
            f"--queues={queues}",
            *embedded_beat,
        )

    def seed_initial_environment(self) -> None:
        result = _run(
            "docker",
            "exec",
            "-i",
            self.db,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "wes",
            "-d",
            "wes",
            input_text=_render_seed(self.ecs_port),
        )
        assert "ERROR" not in result.stderr

    def query(self, sql: str) -> str:
        return _run("docker", "exec", self.db, "psql", "-U", "wes", "-d", "wes", "-At", "-c", sql).stdout.strip()

    def wait_query(self, sql: str, expected: str, *, timeout: float = API_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        actual = ""
        while time.monotonic() < deadline:
            actual = self.query(sql)
            if actual == expected:
                return
            time.sleep(0.5)
        raise AssertionError(f"query did not reach {expected!r}; actual={actual!r}\n{self.diagnostics()}")

    def diagnostics(self) -> str:
        state = self.query(
            "SELECT 'execution=' || coalesce(string_agg(execution_code || ':' || status || ':' || "
            "last_transition_reason, ','), 'none') "
            "FROM wes_biz.material_executions; "
            "SELECT 'evidence=' || coalesce(string_agg(kind || ':' || apply_status || ':attempts=' || "
            "decision_attempt_count::text, ','), 'none') FROM wes_biz.inbound_evidences; "
            "SELECT 'commands=' || coalesce(string_agg(task_type || ':' || status || ':' || count, ','), 'none') "
            "FROM (SELECT task_type, status, count(*)::text AS count FROM wes_biz.device_commands "
            "GROUP BY task_type, status ORDER BY task_type, status) grouped; "
            "SELECT 'confirmations=' || coalesce(string_agg(operation || ':' || status, ','), 'none') "
            "FROM wes_biz.wms_confirmations;"
        )
        chunks = [f"[database]\n{state}"]
        for name in (self.api, self.worker, self.fulfillment):
            complete_log, log_path = self._complete_log(name)
            lines = complete_log.splitlines()
            failure_indexes = [
                index
                for index, line in enumerate(lines)
                if "fact_processing_failed" in line or "ERROR" in line or "Traceback" in line
            ]
            excerpt = lines[failure_indexes[-1] : failure_indexes[-1] + 240] if failure_indexes else lines[-60:]
            chunks.append(f"[{name}] complete_log={log_path}\n" + "\n".join(excerpt))
        return "\n".join(chunks)

    def worker_logs(self) -> str:
        return self._complete_log(self.worker)[0]

    def log_occurrences(self, name: str, marker: str) -> int:
        return sum(marker in line and " succeeded in " in line for line in self._complete_log(name)[0].splitlines())

    def wait_log_occurrences(
        self,
        name: str,
        marker: str,
        minimum: int,
        *,
        timeout: float = API_TIMEOUT_SECONDS,
    ) -> None:
        deadline = time.monotonic() + timeout
        actual = 0
        while time.monotonic() < deadline:
            actual = self.log_occurrences(name, marker)
            if actual >= minimum:
                return
            if _run("docker", "inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip() != "true":
                break
            time.sleep(0.5)
        raise AssertionError(f"log marker {marker!r} did not reach {minimum}; actual={actual}\n{self.diagnostics()}")

    def persist_logs(self) -> None:
        for name in (self.api, self.worker, self.fulfillment):
            self._complete_log(name)

    @staticmethod
    def _complete_log(name: str) -> tuple[str, Path]:
        result = _run("docker", "logs", name, check=False)
        complete_log = result.stdout + result.stderr
        log_path = Path(tempfile.gettempdir()) / f"{name}.log"
        log_path.write_text(complete_log, encoding="utf-8")
        return complete_log, log_path

    def close(self) -> None:
        for name in self.names:
            _run("docker", "rm", "-f", name, check=False)
        _run("docker", "network", "rm", self.network, check=False)
        self.tempdir.cleanup()

    def _assert_current_image(self) -> None:
        inspected = _run("docker", "image", "inspect", BACKEND_IMAGE, check=False)
        assert inspected.returncode == 0, f"missing prebuilt E2E image {BACKEND_IMAGE}; build is a separate gate"
        metadata = json.loads(inspected.stdout)
        assert isinstance(metadata, list)
        assert len(metadata) == 1
        image = metadata[0]
        image_id = image.get("Id")
        labels = image.get("Config", {}).get("Labels") or {}
        expected_revision = _run("git", "rev-parse", "HEAD").stdout.strip()
        expected_source_tree = _run("git", "rev-parse", "HEAD^{tree}").stdout.strip()
        assert isinstance(image_id, str)
        assert image_id.startswith("sha256:")
        assert len(image_id) == 71
        assert labels.get("org.opencontainers.image.revision") == expected_revision
        assert labels.get("com.zontec.wes.source-manifest") == expected_source_tree
        self.image = image_id

    def _wait_command(self, name: str, *command: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _run("docker", "exec", name, *command, check=False).returncode == 0:
                return
            time.sleep(0.5)
        raise AssertionError(f"container {name} did not become ready")

    def _wait_http(self, url: str) -> None:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                if _json_request(url).get("status") == "ok":
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise AssertionError(f"API did not become healthy\n{self.diagnostics()}")

    def _wait_log(self, name: str, marker: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = _run("docker", "logs", name, check=False)
            if marker in result.stdout or marker in result.stderr:
                return
            if _run("docker", "inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip() != "true":
                break
            time.sleep(0.5)
        raise AssertionError(f"worker {name} did not become ready\n{self.diagnostics()}")


def test_worker_logs_preserves_celery_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = object.__new__(_DockerStack)
    stack.worker = "rs9-e2e-worker-test"
    monkeypatch.setitem(
        globals(),
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout="worker banner\n",
            stderr="Scheduler: Sending due task process-device-evidence-batch\n",
        ),
    )

    assert stack.worker_logs() == ("worker banner\nScheduler: Sending due task process-device-evidence-batch\n")


def _provenance_run(
    *,
    revision: str | None = "a" * 40,
    source_tree: str | None = "b" * 40,
):
    image_id = f"sha256:{'c' * 64}"

    def run(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, stdout=f"{'a' * 40}\n", stderr="")
        if args == ("git", "rev-parse", "HEAD^{tree}"):
            return subprocess.CompletedProcess(args, 0, stdout=f"{'b' * 40}\n", stderr="")
        if args == ("docker", "image", "inspect", BACKEND_IMAGE):
            labels = {}
            if revision is not None:
                labels["org.opencontainers.image.revision"] = revision
            if source_tree is not None:
                labels["com.zontec.wes.source-manifest"] = source_tree
            payload = json.dumps([{"Id": image_id, "Config": {"Labels": labels}}])
            return subprocess.CompletedProcess(args, 0, stdout=payload, stderr="")
        raise AssertionError(f"unexpected command: {args}")

    return image_id, run


def test_current_image_is_frozen_to_the_committed_revision_and_source_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = object.__new__(_DockerStack)
    image_id, run = _provenance_run()
    monkeypatch.setitem(globals(), "_run", run)

    stack._assert_current_image()

    assert stack.image == image_id


@pytest.mark.parametrize(
    ("revision", "source_tree"),
    [
        (None, "b" * 40),
        ("d" * 40, "b" * 40),
        ("a" * 40, None),
        ("a" * 40, "d" * 40),
    ],
)
def test_current_image_rejects_missing_or_drifted_provenance(
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    source_tree: str | None,
) -> None:
    stack = object.__new__(_DockerStack)
    _image_id, run = _provenance_run(revision=revision, source_tree=source_tree)
    monkeypatch.setitem(globals(), "_run", run)

    with pytest.raises(AssertionError):
        stack._assert_current_image()


def test_ecs_status_timestamp_is_recent_past_despite_one_millisecond_sampling_skew() -> None:
    wes_observed_at_seconds = 1_787_006_482.863438
    ecs_request_at_seconds = 1_787_006_482.864
    wes_observed_at_ms = int(wes_observed_at_seconds * 1000)

    assert int(ecs_request_at_seconds * 1000) == wes_observed_at_ms + 1
    status_timestamp_ms = _recent_past_timestamp_ms(ecs_request_at_seconds)
    assert 0 < wes_observed_at_ms - status_timestamp_ms < 600_000


def test_render_seed_contains_static_configuration_but_no_epoch_placeholders() -> None:
    rendered = _render_seed(18080)

    assert "__ROUGH_SORTER_CONFIG__" not in rendered
    assert "__ECS_ENDPOINT__" not in rendered
    assert '"MEASUREMENT_DEVICE"' in rendered
    assert "http://ecs-stub:18080" in rendered


def test_wms_wait_creates_new_due_operation_without_device_command_then_closes() -> None:
    accept_release = threading.Event()
    state = _BoundaryState(admission_results=["WAIT", "ACCEPT"], admission_accept_release=accept_release)
    with _serve(_WmsStubHandler, state) as wms_port, _serve(_EcsStubHandler, state) as ecs_port:
        stack = _DockerStack(wms_port=wms_port, ecs_port=ecs_port)
        try:
            stack.start()
            state.api_url = f"http://127.0.0.1:{stack.api_port}"
            stack.seed_initial_environment()
            _start_workline(stack, state.api_url)
            assert _json_request(f"{state.api_url}/api/v1/callback/event", _measurement_scan())["code"] == 200

            deadline = time.monotonic() + API_TIMEOUT_SECONDS
            admission_requests: list[dict[str, Any]] = []
            while time.monotonic() < deadline:
                with state.lock:
                    admission_requests = [
                        item
                        for item in state.wms_requests
                        if item["operation"] == "inbound.material.admission_decide@v1"
                    ]
                if len(admission_requests) == 2:
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"follow-up admission request was not observed\n{stack.diagnostics()}")

            assert (
                stack.query(
                    "SELECT count(*) FROM wes_biz.wms_confirmations "
                    "WHERE operation = 'inbound.material.admission_decide@v1' AND status = 'COMPLETED'"
                )
                == "1"
            )
            assert stack.query("SELECT count(*) FROM wes_biz.device_commands") == "0"
            assert len({item["operation_id"] for item in admission_requests}) == 2

            accept_release.set()
            stack.wait_query(
                f"SELECT status FROM wes_biz.material_executions WHERE material_trace_id = '{TRACE_ID}'",
                "CLOSED",
            )
            assert state.callback_errors == []
            assert (
                stack.query(
                    "SELECT count(*) || ':' || count(DISTINCT operation_id) "
                    "FROM wes_biz.wms_confirmations "
                    "WHERE operation = 'inbound.material.admission_decide@v1'"
                )
                == "2:2"
            )
        finally:
            accept_release.set()
            stack.close()


def test_ecs_ack_does_not_replay_command_while_callback_is_withheld() -> None:
    callback_release = threading.Event()
    state = _BoundaryState(ecs_callback_release=callback_release)
    with _serve(_WmsStubHandler, state) as wms_port, _serve(_EcsStubHandler, state) as ecs_port:
        stack = _DockerStack(wms_port=wms_port, ecs_port=ecs_port)
        try:
            stack.start()
            state.api_url = f"http://127.0.0.1:{stack.api_port}"
            stack.seed_initial_environment()
            _start_workline(stack, state.api_url)
            assert _json_request(f"{state.api_url}/api/v1/callback/event", _measurement_scan())["code"] == 200

            command_state_sql = (
                "SELECT status || ':' || attempt_count::text FROM wes_biz.device_commands ORDER BY created_at LIMIT 1"
            )
            stack.wait_query(command_state_sql, "ACKNOWLEDGED:1")
            with state.lock:
                assert len(state.ecs_commands) == 1
                first_command_code = state.ecs_commands[0]["command_code"]

            marker = "Task src.celery_app.tasks.device_command.dispatch_device_commands_batch"
            completed_before = stack.log_occurrences(stack.worker, marker)
            # ACK can become visible before the current dispatch logs success. Two later completions guarantee that
            # at least one full dispatch run happened after the ACK-producing run.
            stack.wait_log_occurrences(stack.worker, marker, completed_before + 2)

            assert stack.query(command_state_sql) == "ACKNOWLEDGED:1"
            with state.lock:
                assert [item["command_code"] for item in state.ecs_commands] == [first_command_code]

            callback_release.set()
            stack.wait_query(
                f"SELECT status FROM wes_biz.material_executions WHERE material_trace_id = '{TRACE_ID}'",
                "CLOSED",
            )
            stack.persist_logs()
            assert state.callback_errors == []
        finally:
            callback_release.set()
            stack.close()


def test_installed_plugin_closes_one_material_through_public_ingress_and_real_workers() -> None:
    """Prove the installed plugin loop, not supplier or onsite acceptance."""

    state = _BoundaryState()
    with _serve(_WmsStubHandler, state) as wms_port, _serve(_EcsStubHandler, state) as ecs_port:
        stack = _DockerStack(wms_port=wms_port, ecs_port=ecs_port)
        try:
            stack.start()
            state.api_url = f"http://127.0.0.1:{stack.api_port}"
            stack.seed_initial_environment()
            _start_workline(stack, state.api_url)
            assert _json_request(f"{state.api_url}/api/v1/callback/event", _measurement_scan())["code"] == 200

            deadline = time.monotonic() + API_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                execution_status = stack.query(
                    f"SELECT status FROM wes_biz.material_executions WHERE material_trace_id = '{TRACE_ID}'"
                )
                if execution_status == "CLOSED":
                    break
                if execution_status == "RECONCILING":
                    with state.lock:
                        boundary = f"wms={state.wms_requests!r}\necs={state.ecs_commands!r}"
                    pytest.fail(f"business loop reconciled\n{boundary}\n{stack.diagnostics()}")
                if state.callback_errors:
                    break
                time.sleep(0.5)
            else:
                with state.lock:
                    boundary = f"wms={state.wms_requests!r}\necs={state.ecs_commands!r}"
                pytest.fail(f"business loop did not close\n{boundary}\n{stack.diagnostics()}")

            stack.persist_logs()
            assert state.callback_errors == []
            assert (
                stack.query(
                    "SELECT count(*) FROM wes_biz.material_executions "
                    f"WHERE material_trace_id = '{TRACE_ID}' AND status = 'CLOSED'"
                )
                == "1"
            )
            assert (
                stack.query(
                    "SELECT count(*) || ':' || count(DISTINCT command_code) || ':' || "
                    "count(*) FILTER (WHERE status = 'SUCCEEDED') FROM wes_biz.device_commands"
                )
                == "3:3:3"
            )
            assert (
                stack.query(
                    "SELECT count(*) || ':' || count(DISTINCT operation || ':' || operation_id) || ':' || "
                    "count(*) FILTER (WHERE status = 'COMPLETED') FROM wes_biz.wms_confirmations"
                )
                == "3:3:3"
            )
            assert (
                stack.query("SELECT count(*) = count(DISTINCT source_identity) FROM wes_biz.inbound_evidences") == "t"
            )
            worker_logs = stack.worker_logs()
            assert "Scheduler: Sending due task process-device-evidence-batch" in worker_logs
            assert "src.celery_app.tasks.device_command.process_device_evidence_batch" in worker_logs

            with state.lock:
                wms_requests = tuple(state.wms_requests)
                ecs_commands = tuple(state.ecs_commands)
            assert [item["operation"] for item in wms_requests] == [
                "inbound.material.admission_decide@v1",
                "inbound.material.target_decide@v1",
                "inbound.material.placement_report@v1",
            ]
            assert [item["task_type"] for item in ecs_commands] == ["PICK_AND_PUT", "MOVE_FORWARD", "PICK_AND_PUT"]
            assert len({item["command_code"] for item in ecs_commands}) == 3
            assert wms_requests[1]["operation_id"] == ecs_commands[1]["command_code"]
            assert wms_requests[2]["operation_id"] == ecs_commands[2]["command_code"]
        finally:
            stack.close()
