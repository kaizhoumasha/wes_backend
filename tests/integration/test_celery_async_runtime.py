"""Celery prefork 子进程单异步运行时行为合同。"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

_REAL_ASYNCIO_RUNNER = asyncio.Runner


@contextmanager
def _sync_watchdog(timeout: float, operation: str) -> Any:
    """用进程内定时信号中断永久阻塞，并完整恢复调用方信号状态。"""
    entered_at = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    previous_deadline = entered_at + previous_timer[0] if previous_timer[0] > 0 else None
    watchdog_deadline = entered_at + timeout
    previous_has_priority = previous_deadline is not None and previous_deadline <= watchdog_deadline
    effective_deadline = previous_deadline if previous_has_priority else watchdog_deadline
    previous_triggered = False

    def fail_on_timeout(signum: int, frame: Any) -> None:
        nonlocal previous_triggered
        if previous_has_priority:
            previous_triggered = True
            if callable(previous_handler):
                previous_handler(signum, frame)
                return
            if previous_handler == signal.SIG_IGN:
                return
            signal.signal(signal.SIGALRM, previous_handler)
            signal.raise_signal(signal.SIGALRM)
            return
        raise AssertionError(f"{operation} 超过 {timeout:.2f}s watchdog，疑似永久阻塞")

    signal.signal(signal.SIGALRM, fail_on_timeout)
    signal.setitimer(signal.ITIMER_REAL, max(effective_deadline - time.monotonic(), 1e-6))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_deadline is not None and not previous_triggered:
            remaining = max(previous_deadline - time.monotonic(), 1e-6)
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])
        elif previous_triggered and previous_timer[1] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[1], previous_timer[1])


def test_sync_watchdog_restores_outer_timer_with_elapsed_time_deducted() -> None:
    with _sync_watchdog(0.40, "outer watchdog"):
        with _sync_watchdog(1.0, "inner watchdog"):
            time.sleep(0.10)
        remaining, interval = signal.getitimer(signal.ITIMER_REAL)

        assert remaining == pytest.approx(0.30, abs=0.06)
        assert interval == 0.0


def test_sync_watchdog_forwards_an_earlier_outer_timer_without_rearming_it() -> None:
    original_handler = signal.getsignal(signal.SIGALRM)
    original_timer = signal.getitimer(signal.ITIMER_REAL)
    outer_calls: list[int] = []

    def outer_handler(signum: int, _frame: Any) -> None:
        outer_calls.append(signum)

    signal.signal(signal.SIGALRM, outer_handler)
    signal.setitimer(signal.ITIMER_REAL, 0.05)
    try:
        with _sync_watchdog(0.40, "inner watchdog"):
            time.sleep(0.10)

        assert outer_calls == [signal.SIGALRM]
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0.0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, original_handler)
        if original_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *original_timer)


def _runtime_module() -> ModuleType:
    try:
        return importlib.import_module("src.celery_app.async_runtime")
    except ModuleNotFoundError as exc:
        if exc.name == "src.celery_app.async_runtime":
            pytest.fail("缺少批准计划要求的 src.celery_app.async_runtime 单异步运行时")
        raise


def _patch_infrastructure(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> SimpleNamespace:
    from deployment import rough_sorter_composition
    from src.app.device import composition as device_composition
    from src.app.transport import composition as transport_composition
    from src.database import db as db_module
    from src.database import redis_client as redis_module

    infra = SimpleNamespace(
        init_db=AsyncMock(),
        close_db=AsyncMock(),
        init_redis=AsyncMock(),
        close_redis=AsyncMock(),
        transport_runtimes=[],
        build_transport_runtime=AsyncMock(),
        device_command_runtimes=[],
        resolve_device_command_runtime_config=MagicMock(return_value=SimpleNamespace(timeout_seconds=3.0)),
        build_device_command_runtime=MagicMock(),
        execution_runtime=object(),
        build_rough_sorter_runtime=MagicMock(),
    )

    def build_transport_runtime(**_: object) -> SimpleNamespace:
        runtime = SimpleNamespace(aclose=AsyncMock(), service=object(), repository=object(), client=object())
        infra.transport_runtimes.append(runtime)
        return runtime

    infra.build_transport_runtime.side_effect = build_transport_runtime

    def build_device_command_runtime(**_: object) -> SimpleNamespace:
        runtime = SimpleNamespace(aclose=AsyncMock(), command_service=object())
        infra.device_command_runtimes.append(runtime)
        return runtime

    infra.build_device_command_runtime.side_effect = build_device_command_runtime
    infra.build_rough_sorter_runtime.return_value = infra.execution_runtime
    redis_manager = SimpleNamespace(
        init_redis=infra.init_redis,
        close_redis=infra.close_redis,
        is_available=True,
    )
    monkeypatch.setattr(db_module, "init_db", infra.init_db)
    monkeypatch.setattr(db_module, "close_db", infra.close_db)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", MagicMock())
    monkeypatch.setattr(redis_module, "redis_manager", redis_manager)
    monkeypatch.setattr(module, "init_db", infra.init_db, raising=False)
    monkeypatch.setattr(module, "close_db", infra.close_db, raising=False)
    monkeypatch.setattr(module, "redis_manager", redis_manager, raising=False)
    monkeypatch.setattr(
        transport_composition,
        "build_transport_runtime",
        infra.build_transport_runtime,
    )
    monkeypatch.setattr(
        device_composition,
        "resolve_device_command_runtime_config",
        infra.resolve_device_command_runtime_config,
    )
    monkeypatch.setattr(
        device_composition,
        "build_device_command_runtime",
        infra.build_device_command_runtime,
    )
    monkeypatch.setattr(
        rough_sorter_composition,
        "build_rough_sorter_runtime",
        infra.build_rough_sorter_runtime,
    )
    return infra


def _install_runtime(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> Any:
    runtime = module.CeleryAsyncRuntime()
    monkeypatch.setattr(module, "celery_async_runtime", runtime, raising=False)
    return runtime


def test_runner_generation_publishes_stably_rotates_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    from src.core.conf import settings

    monkeypatch.setattr(settings, "WMS_BASE_URL", "http://localhost:8011")
    monkeypatch.setattr(settings, "TRANSPORT_SUBMIT_PATH", "/api/v1/wes/transport-requests")
    infra = _patch_infrastructure(monkeypatch, module)
    first_runtime = _install_runtime(monkeypatch, module)
    assert first_runtime.runner_generation is None

    first_runtime.initialize()
    from src.core.task_queue_gateway import task_queue_gateway

    device_runtime_kwargs = infra.build_device_command_runtime.call_args.kwargs
    assert device_runtime_kwargs["task_queue_gateway"] is task_queue_gateway
    assert device_runtime_kwargs["timeout_seconds"] == 3.0
    assert "base_url" not in device_runtime_kwargs
    first_transport_runtime = first_runtime.transport_runtime
    assert infra.build_transport_runtime.call_args.kwargs["wms_base_url"] == "http://localhost:8011"
    assert infra.build_transport_runtime.call_args.kwargs["transport_submit_path"] == ("/api/v1/wes/transport-requests")
    first_generation = first_runtime.runner_generation
    assert isinstance(first_generation, str) and first_generation
    first_runtime.initialize()
    assert first_runtime.runner_generation == first_generation
    assert first_runtime.transport_runtime is first_transport_runtime
    assert infra.build_transport_runtime.await_count == 1
    first_runtime.shutdown()
    first_transport_runtime.aclose.assert_awaited_once()
    assert first_runtime.runner_generation is None

    second_runtime = module.CeleryAsyncRuntime()
    second_runtime.initialize()
    assert second_runtime.runner_generation != first_generation
    assert second_runtime.transport_runtime is not first_transport_runtime
    second_runtime.shutdown()


def test_fulfillment_queue_initializes_target_transport_without_device_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "wms-fulfillment")
    runtime = module.CeleryAsyncRuntime()

    runtime.initialize()

    infra.build_transport_runtime.assert_awaited_once()
    infra.build_device_command_runtime.assert_not_called()
    assert runtime.transport_runtime is infra.transport_runtimes[0]
    runtime.shutdown()
    infra.transport_runtimes[0].aclose.assert_awaited_once()


def test_runner_generation_failure_rolls_back_all_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    monkeypatch.setattr(module, "uuid4", MagicMock(side_effect=RuntimeError("generation failed")))

    with pytest.raises(RuntimeError, match="generation failed"):
        runtime.initialize()

    assert runtime.state is module.RuntimeState.NEW
    assert runtime._runner is None
    assert runtime.runner_generation is None
    assert runtime._owner_pid is None
    infra.transport_runtimes[0].aclose.assert_awaited_once()
    infra.close_redis.assert_awaited_once()
    infra.close_db.assert_awaited_once()


async def _discard_expired_awaitable(awaitable: Any) -> None:
    """让 deadline double 同时兼容 coroutine、Task 与 Future。"""
    if inspect.iscoroutine(awaitable):
        awaitable.close()
        return
    if isinstance(awaitable, asyncio.Future):
        awaitable.cancel()
        await asyncio.gather(awaitable, return_exceptions=True)
        return
    cancel = getattr(awaitable, "cancel", None)
    if callable(cancel):
        cancel()


def _configure_lazy_deadline(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    infra: SimpleNamespace,
) -> tuple[_ManualMonotonicClock, list[float], Callable[[], None]]:
    clock = _ManualMonotonicClock()
    timeouts: list[float] = []
    real_monotonic = time.monotonic
    real_wait_for = asyncio.wait_for

    async def init_db() -> None:
        clock.advance(2.25)

    async def redis_hang() -> None:
        await asyncio.Event().wait()

    async def fake_wait_for(awaitable: Any, timeout: float) -> Any:
        timeouts.append(timeout)
        await _discard_expired_awaitable(awaitable)
        clock.advance(timeout)
        raise TimeoutError

    infra.init_db.side_effect = init_db
    infra.init_redis.side_effect = redis_hang
    monkeypatch.setattr(module.time, "monotonic", clock)
    monkeypatch.setattr(module.asyncio, "wait_for", fake_wait_for)

    def restore() -> None:
        monkeypatch.setattr(module.time, "monotonic", real_monotonic)
        monkeypatch.setattr(module.asyncio, "wait_for", real_wait_for)

    return clock, timeouts, restore


def test_concurrent_initialize_waits_for_lifecycle_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    started = threading.Event()
    release = threading.Event()
    thread_errors: list[BaseException] = []

    async def init_db() -> None:
        started.set()
        assert release.wait(timeout=1.0)

    def initialize() -> None:
        try:
            runtime.initialize()
        except BaseException as exc:  # pragma: no cover - assertion below reports the original error
            thread_errors.append(exc)

    infra.init_db.side_effect = init_db
    first_thread = threading.Thread(target=initialize)
    second_thread = threading.Thread(target=initialize)
    first_thread.start()
    try:
        assert started.wait(timeout=1.0)
        second_thread.start()
        assert second_thread.is_alive()
    finally:
        release.set()
        first_thread.join(timeout=1.0)
        second_thread.join(timeout=1.0)
        if not first_thread.is_alive() and not second_thread.is_alive():
            runtime.shutdown()

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert thread_errors == []


def test_shutdown_waits_for_inflight_initialize_and_finishes_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """initialize/shutdown 必须使用同一锁顺序，不能把 CLOSED 再覆盖成 READY。"""

    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    started = threading.Event()
    release = threading.Event()
    thread_errors: list[BaseException] = []

    async def init_db() -> None:
        started.set()
        assert release.wait(timeout=1.0)

    def invoke(operation: Callable[[], None]) -> None:
        try:
            operation()
        except BaseException as exc:  # pragma: no cover - assertion below reports the original error
            thread_errors.append(exc)

    infra.init_db.side_effect = init_db
    initialize_thread = threading.Thread(target=invoke, args=(runtime.initialize,))
    shutdown_thread = threading.Thread(target=invoke, args=(runtime.shutdown,))
    initialize_thread.start()
    assert started.wait(timeout=1.0)
    shutdown_thread.start()
    try:
        assert shutdown_thread.is_alive(), "shutdown must wait for the lifecycle owner instead of racing initialize"
    finally:
        release.set()
        initialize_thread.join(timeout=1.0)
        shutdown_thread.join(timeout=1.0)

    assert not initialize_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert thread_errors == []
    assert runtime.state is module.RuntimeState.CLOSED


def test_run_async_waits_for_shutdown_owner_then_observes_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()
    started = threading.Event()
    release = threading.Event()
    thread_errors: list[BaseException] = []

    async def close_redis() -> None:
        started.set()
        assert release.wait(timeout=1.0)

    def shutdown() -> None:
        try:
            runtime.shutdown()
        except BaseException as exc:  # pragma: no cover - assertion below reports the original error
            thread_errors.append(exc)

    infra.close_redis.side_effect = close_redis
    thread = threading.Thread(target=shutdown)
    thread.start()
    try:
        assert started.wait(timeout=1.0)
        with pytest.raises(RuntimeError, match=r"(?i)CLOSED"):
            runtime.run_async(lambda: asyncio.sleep(0))
    finally:
        release.set()
        thread.join(timeout=1.0)
        if not thread.is_alive():
            runtime.shutdown()

    assert not thread.is_alive()
    assert thread_errors == []
    infra.close_db.assert_awaited_once()


def test_lazy_entry_initializes_once_then_reuses_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    _install_runtime(monkeypatch, module)

    assert module.run_async(lambda: asyncio.sleep(0, result="first")) == "first"
    assert module.run_async(lambda: asyncio.sleep(0, result="second")) == "second"

    infra.init_db.assert_awaited_once()


def test_direct_task_run_uses_bounded_lazy_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    clock, timeouts, restore = _configure_lazy_deadline(monkeypatch, module, infra)
    from src.celery_app.app import celery_app

    @celery_app.task(name="tests.runtime.direct_probe")
    def direct_probe() -> str:
        return module.run_async(lambda: asyncio.sleep(0, result="direct"))

    try:
        assert direct_probe.run() == "direct"
        assert timeouts and timeouts[-1] == pytest.approx(0.75, abs=0.01)
        assert clock.value == pytest.approx(103.0, abs=0.01)
        infra.init_db.assert_awaited_once()
    finally:
        restore()
        runtime.shutdown()


def test_eager_task_apply_uses_bounded_lazy_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    clock, timeouts, restore = _configure_lazy_deadline(monkeypatch, module, infra)
    from src.celery_app.app import celery_app

    @celery_app.task(name="tests.runtime.eager_probe")
    def eager_probe() -> str:
        return module.run_async(lambda: asyncio.sleep(0, result="eager"))

    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        assert eager_probe.apply().get() == "eager"
        assert timeouts and timeouts[-1] == pytest.approx(0.75, abs=0.01)
        assert clock.value == pytest.approx(103.0, abs=0.01)
        infra.init_db.assert_awaited_once()
    finally:
        celery_app.conf.task_always_eager = previous
        restore()
        runtime.shutdown()


def _configure_parent_worker_queues(monkeypatch: pytest.MonkeyPatch, app_module: ModuleType) -> None:
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "celery")
    monkeypatch.setattr(app_module, "_actual_worker_queues", lambda _sender: frozenset({"celery"}))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", None)


def test_worker_init_freezes_target_queues_and_leaves_parent_async_resources_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    _install_runtime(monkeypatch, module)
    from src.celery_app import app as app_module
    from src.database import db as db_module

    monkeypatch.setattr(db_module, "engine", None)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", None)
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    _configure_parent_worker_queues(monkeypatch, app_module)

    app_module.on_worker_init()

    assert app_module._frozen_worker_queues == frozenset({"celery"})
    assert db_module.engine is None
    assert db_module.AsyncSessionLocal is None
    infra.init_db.assert_not_awaited()


def test_worker_init_fails_before_consuming_tasks_when_declared_queues_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from celery.exceptions import WorkerTerminate
    from celery.signals import worker_init

    from src.celery_app import app as app_module

    setup_logger = MagicMock()
    monkeypatch.setattr(app_module, "setup_logger", setup_logger)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "celery")
    monkeypatch.setattr(app_module, "_actual_worker_queues", lambda _sender: frozenset({"device-command"}))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", None)

    with pytest.raises(WorkerTerminate, match="worker queue configuration rejected"):
        worker_init.send(sender=app_module.celery_app)

    setup_logger.assert_not_called()


def test_worker_init_validates_queues_before_logger_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import WorkerTerminate
    from celery.signals import worker_init

    from src.celery_app import app as app_module

    setup_logger = MagicMock(side_effect=OSError("log directory unavailable"))
    monkeypatch.setattr(app_module, "setup_logger", setup_logger)
    _configure_parent_worker_queues(monkeypatch, app_module)

    with pytest.raises(WorkerTerminate, match="worker logging initialization rejected"):
        worker_init.send(sender=app_module.celery_app)

    assert app_module._frozen_worker_queues == frozenset({"celery"})
    setup_logger.assert_called_once_with()


def test_worker_init_fails_closed_when_logger_initialization_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import WorkerTerminate
    from celery.signals import worker_init

    from src.celery_app import app as app_module

    monkeypatch.setattr(app_module, "setup_logger", MagicMock(side_effect=OSError("log directory unavailable")))
    _configure_parent_worker_queues(monkeypatch, app_module)

    with pytest.raises(WorkerTerminate, match="worker logging initialization rejected"):
        worker_init.send(sender=app_module.celery_app)

    assert app_module._frozen_worker_queues == frozenset({"celery"})


def test_worker_init_rejects_fulfillment_queue_without_single_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import WorkerTerminate
    from celery.signals import worker_init

    from src.celery_app import app as app_module

    setup_logger = MagicMock()
    monkeypatch.setattr(app_module, "setup_logger", setup_logger)
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "wms-fulfillment")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "2")
    monkeypatch.setattr(app_module, "_actual_worker_queues", lambda _sender: frozenset({"wms-fulfillment"}))
    monkeypatch.setattr(app_module, "_frozen_worker_queues", None)

    with pytest.raises(WorkerTerminate, match="worker queue configuration rejected"):
        worker_init.send(sender=app_module.celery_app)

    setup_logger.assert_not_called()


def test_worker_process_signal_initializes_child_before_first_message(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    from src.celery_app import app as app_module

    monkeypatch.setattr(app_module, "celery_async_runtime", runtime, raising=False)
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"celery"}))
    app_module.on_worker_process_init()

    assert runtime.run_async(lambda: asyncio.sleep(0, result="signal")) == "signal"
    infra.init_db.assert_awaited_once()
    runtime.shutdown()


def test_runtime_rebuilds_fork_inherited_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()
    inherited_runner = runtime._runner
    inherited_generation = runtime.runner_generation
    inherited_transport_runtime = runtime.transport_runtime
    owner_pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: owner_pid + 1)

    try:
        assert runtime.run_async(lambda: asyncio.sleep(0, result="rebuilt")) == "rebuilt"
        assert runtime.runner_generation != inherited_generation
        assert runtime.transport_runtime is not inherited_transport_runtime
        assert infra.init_db.await_count == 2
        assert infra.build_transport_runtime.await_count == 2
    finally:
        runtime.shutdown()
        if inherited_runner is not None:
            inherited_runner.close()


def test_transport_runtime_build_failure_rolls_back_database_without_publishing_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    infra.build_transport_runtime.side_effect = RuntimeError("transport runtime build failed")

    with pytest.raises(RuntimeError, match="transport runtime build failed"):
        runtime.initialize()

    infra.close_db.assert_awaited_once()
    assert runtime.transport_runtime is None
    assert runtime.state is module.RuntimeState.NEW


def test_each_message_runs_with_a_fresh_context(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    trace_id: ContextVar[str] = ContextVar("celery_test_trace_id", default="unset")

    async def first_message() -> str:
        trace_id.set("message-one")
        await asyncio.sleep(0)
        return trace_id.get()

    assert runtime.run_async(first_message) == "message-one"
    assert runtime.run_async(lambda: asyncio.sleep(0, result=trace_id.get())) == "unset"
    runtime.shutdown()


def test_nested_running_loop_has_stable_error_and_does_not_create_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    created = False

    def factory() -> Any:
        nonlocal created
        created = True
        return asyncio.sleep(0)

    async def nested_call() -> None:
        with pytest.raises(RuntimeError, match=r"CeleryAsyncRuntime.*running event loop"):
            runtime.run_async(factory)

    asyncio.run(nested_call())
    assert created is False


def test_failed_initialization_returns_to_retryable_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    events: list[str] = []
    probes: list[_RunnerProbe] = []

    def runner_factory() -> _RunnerProbe:
        probe = _RunnerProbe(events)
        probes.append(probe)
        return probe

    monkeypatch.setattr(module.asyncio, "Runner", runner_factory)
    runtime = _install_runtime(monkeypatch, module)
    infra.init_db.side_effect = [ConnectionError("database unavailable"), None]

    with pytest.raises(ConnectionError, match="database unavailable"):
        runtime.initialize()

    assert events == ["runner"]
    assert probes[0].get_loop().is_closed()
    assert runtime.run_async(lambda: asyncio.sleep(0, result="recovered")) == "recovered"
    assert len(probes) == 2
    assert infra.init_db.await_count == 2
    runtime.shutdown()


def test_factory_and_task_errors_do_not_poison_next_message(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)

    def failing_factory() -> Any:
        raise ValueError("factory failed")

    async def failing_task() -> None:
        raise LookupError("task failed")

    with pytest.raises(ValueError, match="factory failed"):
        runtime.run_async(failing_factory)
    assert runtime.run_async(lambda: asyncio.sleep(0, result="after factory")) == "after factory"
    with pytest.raises(LookupError, match="task failed"):
        runtime.run_async(failing_task)
    assert runtime.run_async(lambda: asyncio.sleep(0, result="after task")) == "after task"
    runtime.shutdown()


@pytest.mark.parametrize("hanging_stage", ["redis", "database"])
def test_shutdown_really_bounds_a_hanging_stage_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    hanging_stage: str,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    events: list[str] = []
    monkeypatch.setattr(module.asyncio, "Runner", lambda: _RunnerProbe(events))
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()
    never = asyncio.Event()

    async def hang() -> None:
        await never.wait()

    if hanging_stage == "redis":
        infra.close_redis.side_effect = hang
    else:
        infra.close_db.side_effect = hang
    monkeypatch.setattr(module, "SHUTDOWN_STAGE_TIMEOUT_SECONDS", 0.01, raising=False)
    started_at = time.monotonic()
    with _sync_watchdog(0.50, f"{hanging_stage} shutdown"):
        runtime.shutdown()

    assert time.monotonic() - started_at < 0.20
    infra.close_redis.assert_awaited_once()
    infra.close_db.assert_awaited_once()
    assert events[-1] == "runner"


class _RunnerProbe:
    def __init__(self, events: list[str]) -> None:
        self._runner = _REAL_ASYNCIO_RUNNER()
        self._loop = self._runner.get_loop()
        self._events = events

    def get_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def run(self, coroutine: Any, *, context: Any = None) -> Any:
        return self._runner.run(coroutine, context=context)

    def close(self) -> None:
        self._events.append("runner")
        self._runner.close()


def test_shutdown_bounds_stubborn_pending_task_then_continues_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    events: list[str] = []
    monkeypatch.setattr(module.asyncio, "Runner", lambda: _RunnerProbe(events))
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()
    pending_tasks: list[asyncio.Task[None]] = []

    async def stubborn_background() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()

    async def spawn_background() -> None:
        pending_tasks.append(asyncio.create_task(stubborn_background()))
        await asyncio.sleep(0)

    runtime.run_async(spawn_background)
    monkeypatch.setattr(module, "SHUTDOWN_STAGE_TIMEOUT_SECONDS", 0.01, raising=False)
    started_at = time.monotonic()
    with _sync_watchdog(0.50, "stubborn pending task shutdown"):
        runtime.shutdown()

    assert time.monotonic() - started_at < 0.20
    infra.close_redis.assert_awaited_once()
    infra.close_db.assert_awaited_once()
    assert events[-1] == "runner"


def test_shutdown_cancels_pending_tasks_and_preserves_stage_order_after_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    events: list[str] = []
    monkeypatch.setattr(module.asyncio, "Runner", lambda: _RunnerProbe(events))
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()

    async def background() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            events.append("pending")

    async def spawn_background() -> asyncio.Task[None]:
        task = asyncio.create_task(background())
        await asyncio.sleep(0)
        return task

    pending = runtime.run_async(spawn_background)

    async def close_redis() -> None:
        events.append("redis")
        raise ValueError("redis close failed")

    async def close_db() -> None:
        events.append("database")

    infra.close_redis.side_effect = close_redis
    infra.close_db.side_effect = close_db
    with _sync_watchdog(0.50, "pending task cancellation shutdown"):
        runtime.shutdown()

    assert pending.cancelled()
    assert events == ["pending", "redis", "database", "runner"]


def test_normal_shutdown_uses_no_default_executor_and_closes_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    events: list[str] = []
    probes: list[_RunnerProbe] = []

    def runner_factory() -> _RunnerProbe:
        probe = _RunnerProbe(events)
        probes.append(probe)
        return probe

    monkeypatch.setattr(module.asyncio, "Runner", runner_factory)
    runtime = _install_runtime(monkeypatch, module)
    runtime.run_async(lambda: asyncio.sleep(0))
    loop = probes[0].get_loop()
    assert getattr(loop, "_default_executor", None) is None

    runtime.shutdown()

    assert events[-1] == "runner"
    assert loop.is_closed()


class _ManualMonotonicClock:
    def __init__(self, start: float = 100.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.parametrize(
    ("hanging_stage", "prior_cost", "expected_timeout", "expected_elapsed"),
    [
        ("redis_ping", 2.25, 0.75, 3.0),
        ("redis_cleanup", 1.25, 1.75, 3.0),
        ("redis_ping_cap", 0.5, 1.0, 1.5),
    ],
)
def test_worker_process_init_redis_stages_share_one_three_second_deadline(
    monkeypatch: pytest.MonkeyPatch,
    hanging_stage: str,
    prior_cost: float,
    expected_timeout: float,
    expected_elapsed: float,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    from src.celery_app import app as app_module

    clock = _ManualMonotonicClock()
    timeouts: list[float] = []
    real_monotonic = time.monotonic
    real_wait_for = asyncio.wait_for

    async def init_db() -> None:
        clock.advance(prior_cost)

    async def ping_or_fail() -> None:
        if hanging_stage == "redis_cleanup":
            raise ConnectionError("redis ping failed")
        await asyncio.Event().wait()

    async def cleanup_hang() -> None:
        await asyncio.Event().wait()

    async def fake_wait_for(awaitable: Any, timeout: float) -> Any:
        timeouts.append(timeout)
        if hanging_stage == "redis_cleanup" and len(timeouts) == 1:
            return await awaitable
        await _discard_expired_awaitable(awaitable)
        clock.advance(timeout)
        raise TimeoutError

    infra.init_db.side_effect = init_db
    infra.init_redis.side_effect = ping_or_fail
    infra.close_redis.side_effect = cleanup_hang
    monkeypatch.setattr(module.time, "monotonic", clock)
    monkeypatch.setattr(module.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(app_module, "celery_async_runtime", runtime, raising=False)
    monkeypatch.setattr(app_module, "setup_logger", MagicMock())
    monkeypatch.setattr(app_module, "_frozen_worker_queues", frozenset({"celery"}))

    try:
        app_module.on_worker_process_init()
        assert timeouts[-1] == pytest.approx(expected_timeout, abs=0.01)
        assert clock.value == pytest.approx(100.0 + expected_elapsed, abs=0.01)
        monkeypatch.setattr(module.time, "monotonic", real_monotonic)
        monkeypatch.setattr(module.asyncio, "wait_for", real_wait_for)
        assert runtime.run_async(lambda: asyncio.sleep(0, result="degraded but ready")) == "degraded but ready"
    finally:
        monkeypatch.setattr(module.time, "monotonic", real_monotonic)
        monkeypatch.setattr(module.asyncio, "wait_for", real_wait_for)
        runtime.shutdown()


def test_worker_process_shutdown_signal_is_registered_and_invokes_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime_module()
    from celery.signals import worker_process_shutdown

    from src.celery_app import app as app_module

    assert hasattr(app_module, "on_worker_process_shutdown"), "必须注册 worker_process_shutdown 生命周期处理器"
    receivers = [receiver() for _key, receiver in worker_process_shutdown.receivers if receiver() is not None]
    assert app_module.on_worker_process_shutdown in receivers

    runtime = SimpleNamespace(shutdown=MagicMock())
    monkeypatch.setattr(app_module, "celery_async_runtime", runtime, raising=False)
    app_module.on_worker_process_shutdown()
    runtime.shutdown.assert_called_once_with()


def test_redis_init_timeout_does_not_wait_forever_for_cancel_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    probe = _PendingInitRunnerProbe()
    monkeypatch.setattr(module.asyncio, "Runner", lambda: probe)
    runtime = _install_runtime(monkeypatch, module)
    keep_resisting = True

    async def cancellation_resistant_init() -> None:
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if keep_resisting:
                    continue
                raise

    infra.init_redis.side_effect = cancellation_resistant_init
    monkeypatch.setattr(module, "INITIALIZATION_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(module, "REDIS_PING_TIMEOUT_SECONDS", 0.01)

    started_at = time.monotonic()
    try:
        with _sync_watchdog(0.50, "Redis cancellation cleanup during worker init"):
            with pytest.raises(TimeoutError, match="ignored cancellation"):
                runtime.initialize()

        assert time.monotonic() - started_at < 0.20
        assert runtime.state is module.RuntimeState.CLOSED
        with pytest.raises(RuntimeError, match="CLOSED"):
            runtime.run_async(lambda: asyncio.sleep(0, result="must restart"))
        runtime.shutdown()
    finally:
        keep_resisting = False
        for task in asyncio.all_tasks(probe.get_loop()):
            task.cancel()
        probe.run(asyncio.sleep(0))
        probe.force_close()


def test_redis_timeout_reaches_ready_only_after_top_level_init_task_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    from src.database import redis_client as redis_module

    manager = redis_module.RedisManager()
    init_tasks: list[asyncio.Task[None]] = []
    candidate_pool = SimpleNamespace(disconnect=AsyncMock())

    async def hanging_ping() -> None:
        current = asyncio.current_task()
        assert current is not None
        init_tasks.append(current)
        await asyncio.Event().wait()

    async def hanging_candidate_close() -> None:
        await asyncio.Event().wait()

    candidate_client = SimpleNamespace(
        ping=AsyncMock(side_effect=hanging_ping),
        close=AsyncMock(side_effect=hanging_candidate_close),
    )
    pool_factory = SimpleNamespace(from_url=MagicMock(return_value=candidate_pool))
    monkeypatch.setattr(redis_module, "ConnectionPool", pool_factory)
    monkeypatch.setattr(redis_module, "Redis", MagicMock(return_value=candidate_client))
    monkeypatch.setattr(module, "redis_manager", manager)
    runtime = _install_runtime(monkeypatch, module)
    monkeypatch.setattr(module, "INITIALIZATION_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(module, "REDIS_PING_TIMEOUT_SECONDS", 0.01)

    with _sync_watchdog(0.50, "Redis top-level init cancellation"):
        runtime.initialize()

    assert runtime.state is module.RuntimeState.READY
    assert len(init_tasks) == 1
    assert init_tasks[0].done()
    assert manager.redis_client is None
    assert manager.connection_pool is None
    assert manager.is_available is False

    assert runtime.run_async(lambda: asyncio.sleep(0, result="next message")) == "next message"
    assert init_tasks[0].done()
    assert manager.redis_client is None
    assert manager.connection_pool is None
    infra.close_db.assert_not_awaited()
    runtime.shutdown()


def test_database_init_timeout_does_not_wait_forever_for_cancel_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    probe = _PendingInitRunnerProbe()
    monkeypatch.setattr(module.asyncio, "Runner", lambda: probe)
    runtime = _install_runtime(monkeypatch, module)

    async def cancellation_resistant_init() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()

    infra.init_db.side_effect = cancellation_resistant_init
    monkeypatch.setattr(module, "INITIALIZATION_TIMEOUT_SECONDS", 0.01)

    started_at = time.monotonic()
    try:
        with _sync_watchdog(0.50, "database cancellation cleanup during worker init"):
            with pytest.raises(TimeoutError):
                runtime.initialize()

        assert time.monotonic() - started_at < 0.20
        assert runtime.state is module.RuntimeState.CLOSED
    finally:
        for task in asyncio.all_tasks(probe.get_loop()):
            task.cancel()
        probe.run(asyncio.sleep(0))
        probe.force_close()


def test_database_timeout_observes_one_tick_cancellation_before_allowing_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    attempts = 0
    cleanup_events: list[str] = []

    async def init_db() -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            return
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            cleanup_events.append("database")
            raise

    infra.init_db.side_effect = init_db
    monkeypatch.setattr(module, "INITIALIZATION_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        runtime.initialize()

    assert cleanup_events == ["database"]
    assert runtime.state is module.RuntimeState.NEW
    assert runtime._abandoned_runners == []
    assert runtime.run_async(lambda: asyncio.sleep(0, result="retried")) == "retried"
    assert infra.init_db.await_count == 2
    runtime.shutdown()


def test_shutdown_continues_after_cleanup_ignores_first_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()
    events: list[str] = []

    async def stubborn_redis_close() -> None:
        events.append("redis")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()

    async def close_db() -> None:
        events.append("database")

    infra.close_redis.side_effect = stubborn_redis_close
    infra.close_db.side_effect = close_db
    monkeypatch.setattr(module, "SHUTDOWN_STAGE_TIMEOUT_SECONDS", 0.01)

    started_at = time.monotonic()
    with _sync_watchdog(0.50, "cancellation-resistant Redis shutdown"):
        runtime.shutdown()

    assert time.monotonic() - started_at < 0.20
    assert events == ["redis", "database"]


def test_failed_init_after_database_publish_rolls_back_in_order_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    events: list[str] = []

    class NonDegradableInitError(BaseException):
        pass

    infra.init_redis.side_effect = [NonDegradableInitError("fatal init"), None]

    async def close_redis() -> None:
        events.append("redis")

    async def close_db() -> None:
        events.append("database")

    infra.close_redis.side_effect = close_redis
    infra.close_db.side_effect = close_db

    with pytest.raises(NonDegradableInitError, match="fatal init"):
        runtime.initialize()

    assert events == ["redis", "database"]
    assert runtime.state is module.RuntimeState.NEW
    assert runtime._owner_pid is None
    assert runtime.run_async(lambda: asyncio.sleep(0, result="retried")) == "retried"
    assert infra.init_db.await_count == 2
    assert infra.init_redis.await_count == 2
    runtime.shutdown()


def test_failed_init_rollback_gives_every_cleanup_stage_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    events: list[str] = []

    async def record(name: str) -> None:
        await asyncio.sleep(0.01)
        events.append(name)

    async def close_redis() -> None:
        await record("redis")

    async def close_database() -> None:
        await record("database")

    device_command_runtime = SimpleNamespace(aclose=lambda: record("device-command"))
    transport_runtime = SimpleNamespace(aclose=lambda: record("transport"))
    infra.close_redis.side_effect = close_redis
    infra.close_db.side_effect = close_database
    monkeypatch.setattr(module, "SHUTDOWN_STAGE_TIMEOUT_SECONDS", 0.05)

    asyncio.run(
        module.CeleryAsyncRuntime._rollback_failed_initialization(
            transport_runtime=transport_runtime,
            device_command_runtime=device_command_runtime,
        )
    )

    assert events == ["redis", "device-command", "transport", "database"]


def test_normal_shutdown_permanently_rejects_initialize_and_run_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = _install_runtime(monkeypatch, module)
    factory = MagicMock()

    runtime.initialize()
    runtime.shutdown()

    for _ in range(2):
        with pytest.raises(RuntimeError, match="CLOSED"):
            runtime.initialize()
        with pytest.raises(RuntimeError, match="CLOSED"):
            runtime.run_async(factory)
    factory.assert_not_called()


class _PendingInitRunnerProbe:
    def __init__(self) -> None:
        self._runner = _REAL_ASYNCIO_RUNNER()
        self.close = MagicMock()

    def get_loop(self) -> asyncio.AbstractEventLoop:
        return self._runner.get_loop()

    def run(self, coroutine: Any, *, context: Any = None) -> Any:
        return self._runner.run(coroutine, context=context)

    def force_close(self) -> None:
        self._runner.close()


def test_failed_init_skips_runner_close_when_cancel_resistant_task_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    probe = _PendingInitRunnerProbe()
    runner_factory = MagicMock(return_value=probe)
    monkeypatch.setattr(module.asyncio, "Runner", runner_factory)
    runtime = _install_runtime(monkeypatch, module)
    keep_resisting = True

    async def cancellation_resistant_init() -> None:
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if keep_resisting:
                    continue
                raise

    infra.init_db.side_effect = cancellation_resistant_init
    monkeypatch.setattr(module, "INITIALIZATION_TIMEOUT_SECONDS", 0.01)

    started_at = time.monotonic()
    try:
        with _sync_watchdog(0.50, "cancel-resistant task during failed initialization"):
            with pytest.raises(TimeoutError):
                runtime.initialize()

        assert time.monotonic() - started_at < 0.20
        probe.close.assert_not_called()
        assert runtime.state is module.RuntimeState.CLOSED
        assert runtime._runner is None
        assert runtime._owner_pid is None
        assert runtime._abandoned_runners == [probe]

        for _ in range(3):
            with pytest.raises(RuntimeError, match="CLOSED"):
                runtime.initialize()
            with pytest.raises(RuntimeError, match="CLOSED"):
                runtime.run_async(lambda: asyncio.sleep(0))
        runner_factory.assert_called_once_with()
        assert runtime._abandoned_runners == [probe]
    finally:
        keep_resisting = False
        for task in asyncio.all_tasks(probe.get_loop()):
            task.cancel()
        probe.run(asyncio.sleep(0))
        probe.force_close()


class _ShutdownRunError(BaseException):
    pass


class _ShutdownFaultRunnerProbe(_RunnerProbe):
    def __init__(self, events: list[str], failure_call: int) -> None:
        super().__init__(events)
        self.failure_call = failure_call
        self.shutdown_run_calls = 0
        self.armed = False

    def run(self, coroutine: Any, *, context: Any = None) -> Any:
        if self.armed:
            self.shutdown_run_calls += 1
            if self.shutdown_run_calls == self.failure_call:
                if inspect.iscoroutine(coroutine):
                    coroutine.close()
                raise _ShutdownRunError(f"shutdown stage {self.failure_call}")
        return super().run(coroutine, context=context)


@pytest.mark.parametrize("failure_call", range(1, 7))
def test_shutdown_contains_each_runner_run_failure_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    events: list[str] = []
    probe = _ShutdownFaultRunnerProbe(events, failure_call)
    monkeypatch.setattr(module.asyncio, "Runner", lambda: probe)
    runtime = _install_runtime(monkeypatch, module)
    runtime.initialize()
    probe.armed = True

    with _sync_watchdog(0.50, f"shutdown runner.run failure {failure_call}"):
        runtime.shutdown()

    assert probe.shutdown_run_calls == 6
    if failure_call != 2:
        infra.close_redis.assert_awaited_once()
    if failure_call != 3:
        infra.transport_runtimes[0].aclose.assert_awaited_once()
    if failure_call != 4:
        infra.device_command_runtimes[0].aclose.assert_awaited_once()
    if failure_call != 5:
        infra.close_db.assert_awaited_once()
    assert runtime.state is module.RuntimeState.CLOSED
    assert runtime._runner is None
    assert runtime._owner_pid is None

    runtime.shutdown()
    assert probe.shutdown_run_calls == 6
