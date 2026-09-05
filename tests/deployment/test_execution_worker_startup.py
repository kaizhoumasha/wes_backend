import asyncio
import os
import subprocess
import time
from inspect import signature
from multiprocessing import Value
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.transport.composition import build_transport_runtime
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService
from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime
from src.celery_app.config import beat_schedule, task_routes
from src.celery_app.tasks import execution
from src.celery_app.tasks import safety as safety_tasks
from src.core.conf import Settings
from tests.support import ecs_uniform_wire
from tests.support.ecs_uniform_wire import (
    DEVICE_COMMAND_QUEUE,
    DEVICE_COMMAND_STARTUP_PROBE_TASK,
    DEVICE_COMMAND_STARTUP_PROBE_TOKEN,
    DeviceCommandBrokerWorker,
)

TASK_NAME = "src.celery_app.tasks.execution.process_execution_facts_batch"
DEVICE_COMMAND_BEAT_CONTRACTS = (
    (
        "dispatch-device-commands-batch",
        "src.celery_app.tasks.device_command.dispatch_device_commands_batch",
        10.0,
        10.0,
    ),
    (
        "process-device-evidence-batch",
        "src.celery_app.tasks.device_command.process_device_evidence_batch",
        10.0,
        10.0,
    ),
    (
        "reconcile-device-commands-batch",
        "src.celery_app.tasks.device_command.reconcile_device_commands_batch",
        30.0,
        30.0,
    ),
)


def test_celery_entrypoint_starts_without_retired_wms_process_role(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_celery = fake_bin / "celery"
    fake_celery.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    fake_celery.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CELERY_WORKER_QUEUES": "wms-fulfillment",
        "CELERY_WORKER_CONCURRENCY": "1",
    }
    environment.pop("WMS_PROVIDER_PROCESS_ROLE", None)

    completed = subprocess.run(
        ["/bin/sh", "docker/test/celery.entrypoint.sh"],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--concurrency=1" in completed.stdout
    assert "--queues=wms-fulfillment" in completed.stdout


def _assert_target_beat_contract(
    *,
    schedules: dict[str, dict[str, object]],
    routes: dict[str, dict[str, str]],
) -> None:
    execution = schedules.get("process-execution-facts-batch")
    if execution is None or execution.get("task") != TASK_NAME:
        raise ValueError("Beat required schedule is missing or invalid: process-execution-facts-batch")
    if routes.get(TASK_NAME) != {"queue": "device-command"}:
        raise ValueError("execution task must use device-command")
    for schedule_name, task_name, period, expires in DEVICE_COMMAND_BEAT_CONTRACTS:
        expected = {
            "task": task_name,
            "schedule": period,
            "kwargs": {"limit": 100},
            "options": {"expires": expires},
        }
        if schedules.get(schedule_name) != expected or routes.get(task_name) != {"queue": "device-command"}:
            raise ValueError(f"DeviceCommand Beat contract drift: {schedule_name}")


def test_target_wms_composition_accepts_only_explicit_endpoint_inputs() -> None:
    """Transport composition 直接消费最小 endpoint settings，不接收 compiled provider profile。"""

    assert tuple(signature(build_transport_runtime).parameters) == (
        "wms_base_url",
        "transport_submit_path",
        "session_factory",
    )


def test_target_settings_replace_provider_profile_with_minimal_wms_endpoint_keys() -> None:
    fields = set(Settings.model_fields)

    assert {"WMS_BASE_URL", "TRANSPORT_SUBMIT_PATH"} <= fields
    assert fields.isdisjoint(
        {
            "WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES",
            "WMS_EFFECT_ADMISSION_ENABLED",
            "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS",
            "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES",
            "WMS_EFFECT_STATUS_TIMEOUT_SECONDS",
            "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS",
            "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",
            "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2",
            "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1",
            "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2",
            "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V1",
            "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V2",
            "WMS_PROVIDER_PROFILE_FILE",
        }
    )


def _worker_sender(*queues: str) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            amqp=SimpleNamespace(queues=SimpleNamespace(consume_from={queue: object() for queue in queues}))
        )
    )


def test_execution_fact_task_is_registered_and_routed_to_wes_worker() -> None:
    assert execution.process_execution_facts_batch.name == TASK_NAME
    assert TASK_NAME in celery_app.tasks
    assert "src.celery_app.tasks.execution" in celery_app.conf.include
    assert task_routes[TASK_NAME] == {"queue": "device-command"}
    assert beat_schedule["process-execution-facts-batch"] == {
        "task": TASK_NAME,
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    }


def test_safety_drain_task_is_registered_with_bounded_beat_fallback() -> None:
    task_name = "src.celery_app.tasks.workline.drain_safety_incidents_batch"
    celery_app.loader.import_default_modules()

    assert task_name in celery_app.tasks
    assert task_routes[task_name] == {"queue": "celery"}
    assert beat_schedule["drain-safety-incidents-batch"] == {
        "task": task_name,
        "schedule": 10.0,
        "kwargs": {"limit": 10, "command_limit": 100},
        "options": {"expires": 10.0},
    }


@pytest.mark.parametrize(
    ("limit", "command_limit"),
    ((0, 100), (11, 100), (True, 100), (10, 0), (10, 101), (10, True)),
)
def test_safety_drain_task_rejects_unbounded_or_empty_batches(limit: object, command_limit: object) -> None:
    with pytest.raises(ValueError, match="batch limit"):
        safety_tasks.drain_safety_incidents_batch.run(limit=limit, command_limit=command_limit)


@pytest.mark.parametrize("drift", ("missing-schedule", "missing-route", "wrong-route"))
def test_execution_fact_scanner_is_required_by_deployment_attestation(drift: str) -> None:
    schedules = dict(beat_schedule)
    routes = dict(task_routes)
    if drift == "missing-schedule":
        schedules.pop("process-execution-facts-batch")
    elif drift == "missing-route":
        routes.pop(TASK_NAME)
    else:
        routes[TASK_NAME] = {"queue": "celery"}

    with pytest.raises(ValueError, match=r"Beat required schedule|device-command"):
        _assert_target_beat_contract(schedules=schedules, routes=routes)


@pytest.mark.parametrize(("schedule_name", "task_name", "period", "expires"), DEVICE_COMMAND_BEAT_CONTRACTS)
def test_device_command_beat_contract_matches_production_configuration(
    schedule_name: str,
    task_name: str,
    period: float,
    expires: float,
) -> None:
    assert beat_schedule[schedule_name] == {
        "task": task_name,
        "schedule": period,
        "kwargs": {"limit": 100},
        "options": {"expires": expires},
    }
    assert task_routes[task_name] == {"queue": "device-command"}


@pytest.mark.parametrize(("schedule_name", "task_name", "period", "expires"), DEVICE_COMMAND_BEAT_CONTRACTS)
@pytest.mark.parametrize("drift", ("missing-schedule", "wrong-period", "wrong-expires", "missing-route", "wrong-route"))
def test_device_command_beat_drift_fails_deployment_attestation(
    schedule_name: str,
    task_name: str,
    period: float,
    expires: float,
    drift: str,
) -> None:
    schedules = {name: {**value, "options": dict(value.get("options", {}))} for name, value in beat_schedule.items()}
    routes = {name: dict(value) for name, value in task_routes.items()}
    if drift == "missing-schedule":
        schedules.pop(schedule_name)
    elif drift == "wrong-period":
        schedules[schedule_name]["schedule"] = period + 1.0
    elif drift == "wrong-expires":
        schedules[schedule_name]["options"] = {"expires": expires + 1.0}
    elif drift == "missing-route":
        routes.pop(task_name)
    else:
        routes[task_name] = {"queue": "celery"}

    with pytest.raises(ValueError, match="DeviceCommand Beat"):
        _assert_target_beat_contract(schedules=schedules, routes=routes)


def test_execution_task_requires_an_explicit_child_runtime(monkeypatch) -> None:
    monkeypatch.setattr(celery_async_runtime, "_execution_runtime", None)

    try:
        execution._current_processor()
    except RuntimeError as exc:
        assert "Execution runtime is unavailable" in str(exc)
    else:
        raise AssertionError("unbound execution runtime must fail closed")


def test_device_command_startup_probe_returns_child_identity(monkeypatch) -> None:
    monkeypatch.setattr(ecs_uniform_wire.os, "getpid", lambda: 12345)

    assert ecs_uniform_wire.device_command_startup_probe.run() == {
        "token": DEVICE_COMMAND_STARTUP_PROBE_TOKEN,
        "pid": 12345,
    }


def test_device_command_worker_readiness_budget_covers_a_cold_ci_container_start() -> None:
    assert ecs_uniform_wire.WORKER_READY_TIMEOUT_SECONDS >= 60


def test_device_command_worker_reuses_the_locked_test_environment_without_sync(monkeypatch) -> None:
    worker = DeviceCommandBrokerWorker(
        "postgresql+asyncpg://user:password@db:5432/test_device_command",
        "redis://redis:6379/15",
        run_id="no-sync-proof",
    )
    captured_command: list[str] = []
    fake_process = SimpleNamespace(pid=43210)

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured_command.extend(command)
        return fake_process

    monkeypatch.setattr(ecs_uniform_wire.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "_wait_for_startup_probe", lambda deadline: None)

    try:
        worker.start()
        assert captured_command[1:4] == ["run", "--no-sync", "celery"]
    finally:
        worker.producer.close()
        if worker._log_file is not None:
            worker._log_file.close()
        if worker.log_path is not None:
            worker.log_path.unlink(missing_ok=True)


def test_device_command_worker_accepts_build_scoped_compose_redis_on_non_zero_database() -> None:
    worker = DeviceCommandBrokerWorker(
        "postgresql+asyncpg://user:password@db:5432/test_device_command",
        "redis://redis:6379/15",
        run_id="compose-network-proof",
    )

    try:
        assert worker.key_prefix == "it:device-command:compose-network-proof:"
    finally:
        worker.producer.close()


@pytest.mark.parametrize(
    "redis_url",
    (
        "redis://127.0.0.1:6379/0",
        "redis://redis.example.com:6379/15",
    ),
)
def test_device_command_worker_rejects_non_isolated_or_non_local_redis(redis_url) -> None:
    with pytest.raises(AssertionError, match="local/build-scoped non-zero test database"):
        DeviceCommandBrokerWorker(
            "postgresql+asyncpg://user:password@db:5432/test_device_command",
            redis_url,
        )


def test_device_command_worker_readiness_requires_child_probe_after_parent_ready(tmp_path) -> None:
    worker = object.__new__(DeviceCommandBrokerWorker)
    worker.process = MagicMock()
    worker.process.poll.return_value = None
    worker.log_path = tmp_path / "worker.log"
    worker.log_path.write_text("[INFO/MainProcess] celery@localhost ready.\n")
    worker._log_file = MagicMock()
    worker.producer = MagicMock()
    probe = worker.producer.send_task.return_value
    probe.get.return_value = {"token": DEVICE_COMMAND_STARTUP_PROBE_TOKEN, "pid": 12345}

    worker._wait_for_startup_probe(time.monotonic() + 1)

    worker.producer.send_task.assert_called_once_with(
        DEVICE_COMMAND_STARTUP_PROBE_TASK, kwargs={}, queue=DEVICE_COMMAND_QUEUE
    )
    probe.get.assert_called_once()


@pytest.mark.asyncio
async def test_execution_worker_gate_allows_no_active_epoch() -> None:
    repository = type("_EpochRepository", (), {"list_active_plugin_identities": AsyncMock(return_value=[])})()

    await LineRunEpochService(repository=repository).assert_execution_worker_startable(object(), plugins=())


def test_actual_worker_queues_reads_work_controller_sender_app() -> None:
    from src.celery_app import app as app_module

    assert app_module._actual_worker_queues(_worker_sender("default", "device-command")) == frozenset(
        {"default", "device-command"}
    )


def test_actual_worker_queues_falls_back_to_celery_app_for_direct_invocation() -> None:
    from src.celery_app import app as app_module

    assert app_module._actual_worker_queues(None) == frozenset(app_module.celery_app.amqp.queues.consume_from)


@pytest.mark.parametrize(
    "sender",
    [
        SimpleNamespace(),
        SimpleNamespace(app=SimpleNamespace()),
        SimpleNamespace(app=SimpleNamespace(amqp=SimpleNamespace())),
    ],
)
def test_worker_init_rejects_incomplete_work_controller_sender(monkeypatch, sender: SimpleNamespace | None) -> None:
    from celery.exceptions import WorkerTerminate

    from src.celery_app import app as app_module

    monkeypatch.setattr(app_module, "_frozen_worker_queues", None, raising=False)

    with pytest.raises(WorkerTerminate, match="worker queue configuration rejected"):
        app_module.on_worker_init(sender=sender)


def test_worker_init_freezes_actual_queues_when_declaration_matches(monkeypatch) -> None:
    from src.celery_app import app as app_module

    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module, "_frozen_worker_queues", None, raising=False)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "default,celery,device-command")

    app_module.on_worker_init(sender=_worker_sender("default", "celery", "device-command"))

    assert app_module._frozen_worker_queues == frozenset({"default", "celery", "device-command"})


def test_worker_init_rejects_environment_queue_drift(monkeypatch) -> None:
    from celery.exceptions import WorkerTerminate

    from src.celery_app import app as app_module

    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module, "_frozen_worker_queues", None, raising=False)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "default,celery")

    with pytest.raises(WorkerTerminate, match="worker queue configuration rejected"):
        app_module.on_worker_init(sender=_worker_sender("device-command"))


@pytest.mark.parametrize("failure_stage", ["logger", "runtime", "gate"])
def test_worker_process_signal_rejects_any_initialization_failure(monkeypatch, failure_stage: str) -> None:
    from celery.exceptions import WorkerTerminate
    from celery.signals import worker_process_init

    from src.celery_app import app as app_module

    setup_logger = MagicMock()
    initialize = MagicMock()
    gate = AsyncMock()
    if failure_stage == "logger":
        setup_logger.side_effect = RuntimeError("logger failed")
    elif failure_stage == "runtime":
        initialize.side_effect = RuntimeError("runtime failed")
    else:
        gate.side_effect = RuntimeError("gate failed")
    monkeypatch.setattr(app_module, "setup_logger", setup_logger)
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", initialize)
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", lambda factory: asyncio.run(factory()))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"device-command"}), raising=False)
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)

    with pytest.raises(WorkerTerminate, match="worker process initialization rejected"):
        worker_process_init.send(sender=app_module.celery_app)


def test_execution_worker_child_startup_rejects_unfrozen_queues(monkeypatch) -> None:
    from celery.exceptions import WorkerTerminate

    from src.celery_app import app as app_module

    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", MagicMock())
    monkeypatch.setattr(app_module, "_frozen_worker_queues", None, raising=False)

    with pytest.raises(WorkerTerminate, match="worker process initialization rejected"):
        app_module.on_worker_process_init()


def test_execution_worker_child_startup_rejects_epoch_gate_failure(monkeypatch) -> None:
    from celery.exceptions import WorkerTerminate

    from src.celery_app import app as app_module

    gate = AsyncMock(side_effect=ActiveLineRunEpochExistsError("active epoch"))
    initialize = MagicMock()
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", initialize)
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", lambda factory: asyncio.run(factory()))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"device-command"}), raising=False)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "default,celery,device-command")
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)

    with pytest.raises(WorkerTerminate, match="worker process initialization rejected"):
        app_module.on_worker_process_init()

    initialize.assert_called_once_with()
    gate.assert_awaited_once_with()


def test_execution_worker_child_startup_allows_epoch_gate_success(monkeypatch) -> None:
    from src.celery_app import app as app_module

    gate = AsyncMock()
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", lambda factory: asyncio.run(factory()))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"device-command"}), raising=False)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "default,celery,device-command")
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)

    app_module.on_worker_process_init()

    gate.assert_awaited_once_with()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Celery production prefork requires POSIX fork")
def test_replacement_execution_worker_child_does_not_repeat_epoch_restart_gate_across_forks(monkeypatch) -> None:
    from src.celery_app import app as app_module

    gate_calls = Value("i", 0)

    def run_gate_once(_factory: object) -> None:
        with gate_calls.get_lock():
            gate_calls.value += 1

    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", run_gate_once)
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"device-command"}), raising=False)
    app_module._execution_restart_gate_passed.value = False

    child_pids: list[int] = []
    for _ in range(2):
        child_pid = os.fork()
        if child_pid == 0:
            try:
                app_module.on_worker_process_init()
            except BaseException:
                os._exit(1)
            os._exit(0)
        child_pids.append(child_pid)

    statuses = [os.waitpid(child_pid, 0)[1] for child_pid in child_pids]

    assert all(os.waitstatus_to_exitcode(status) == 0 for status in statuses)
    assert gate_calls.value == 1


def test_fulfillment_child_runs_execution_epoch_gate(monkeypatch) -> None:
    from src.celery_app import app as app_module

    initialize = MagicMock()
    gate = AsyncMock()
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", initialize)
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", lambda factory: asyncio.run(factory()))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"wms-fulfillment"}), raising=False)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "wms-fulfillment")
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)
    app_module._execution_restart_gate_passed.value = False

    app_module.on_worker_process_init()

    initialize.assert_called_once_with()
    gate.assert_awaited_once_with()
