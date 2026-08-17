import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.wms_integration.provider_readiness import WmsProviderProcessRole
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService
from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime
from src.celery_app.config import beat_schedule, task_routes
from src.celery_app.tasks import execution
from tests.support import ecs_uniform_wire
from tests.support.ecs_uniform_wire import (
    DEVICE_COMMAND_QUEUE,
    DEVICE_COMMAND_STARTUP_PROBE_TASK,
    DEVICE_COMMAND_STARTUP_PROBE_TOKEN,
    DeviceCommandBrokerWorker,
)

TASK_NAME = "src.celery_app.tasks.execution.process_execution_facts_batch"


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
async def test_execution_worker_gate_rejects_a_persisted_active_epoch() -> None:
    repository = type("_EpochRepository", (), {"has_active_epoch": AsyncMock(return_value=True)})()

    with pytest.raises(ActiveLineRunEpochExistsError, match="execution worker"):
        await LineRunEpochService(repository=repository).assert_execution_worker_startable(object())


def test_execution_worker_child_startup_rejects_epoch_gate_failure(monkeypatch) -> None:
    from celery.exceptions import WorkerTerminate

    from src.celery_app import app as app_module

    gate = AsyncMock(side_effect=ActiveLineRunEpochExistsError("active epoch"))
    initialize = MagicMock()
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", initialize)
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", lambda factory: asyncio.run(factory()))
    monkeypatch.setattr(app_module.celery_async_runtime, "_process_role", WmsProviderProcessRole.WES)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "default,celery,device-command")
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)

    with pytest.raises(WorkerTerminate, match="execution worker startup rejected"):
        app_module.on_worker_process_init()

    initialize.assert_called_once_with()
    gate.assert_awaited_once_with()


def test_execution_worker_child_startup_allows_epoch_gate_success(monkeypatch) -> None:
    from src.celery_app import app as app_module

    gate = AsyncMock()
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", lambda factory: asyncio.run(factory()))
    monkeypatch.setattr(app_module.celery_async_runtime, "_process_role", WmsProviderProcessRole.WES)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "default,celery,device-command")
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)

    app_module.on_worker_process_init()

    gate.assert_awaited_once_with()


def test_fulfillment_child_does_not_run_execution_epoch_gate(monkeypatch) -> None:
    from src.celery_app import app as app_module

    initialize = MagicMock()
    run_async = MagicMock()
    gate = AsyncMock()
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module.celery_async_runtime, "initialize", initialize)
    monkeypatch.setattr(app_module.celery_async_runtime, "run_async", run_async)
    monkeypatch.setattr(app_module.celery_async_runtime, "_process_role", WmsProviderProcessRole.FULFILLMENT)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "wms-fulfillment")
    monkeypatch.setattr(execution, "assert_execution_worker_startable", gate, raising=False)

    app_module.on_worker_process_init()

    initialize.assert_called_once_with()
    run_async.assert_not_called()
    gate.assert_not_called()
