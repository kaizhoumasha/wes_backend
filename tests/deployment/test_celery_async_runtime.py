"""Celery prefork 子进程单异步运行时合同。"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from contextvars import ContextVar
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _runtime_module() -> ModuleType:
    try:
        return importlib.import_module("src.celery_app.async_runtime")
    except ModuleNotFoundError as exc:
        if exc.name == "src.celery_app.async_runtime":
            pytest.fail("缺少批准计划要求的 src.celery_app.async_runtime 单异步运行时")
        raise


def _state_value(runtime: Any) -> str:
    state = runtime.state
    return str(getattr(state, "value", state))


def _patch_infrastructure(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> SimpleNamespace:
    from src.database import db as db_module
    from src.database import redis_client as redis_module

    infra = SimpleNamespace(
        init_db=AsyncMock(),
        close_db=AsyncMock(),
        init_redis=AsyncMock(),
        close_redis=AsyncMock(),
    )
    redis_manager = SimpleNamespace(
        init_redis=infra.init_redis,
        close_redis=infra.close_redis,
        is_available=True,
    )
    monkeypatch.setattr(db_module, "init_db", infra.init_db)
    monkeypatch.setattr(db_module, "close_db", infra.close_db)
    monkeypatch.setattr(redis_module, "redis_manager", redis_manager)
    monkeypatch.setattr(module, "init_db", infra.init_db, raising=False)
    monkeypatch.setattr(module, "close_db", infra.close_db, raising=False)
    monkeypatch.setattr(module, "redis_manager", redis_manager, raising=False)
    return infra


def test_runtime_state_transitions_and_repeated_shutdown_are_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = module.CeleryAsyncRuntime()

    assert _state_value(runtime) == "NEW"
    runtime.initialize()
    assert _state_value(runtime) == "READY"
    runtime.shutdown()
    assert _state_value(runtime) == "CLOSED"
    runtime.shutdown()
    assert _state_value(runtime) == "CLOSED"
    infra.init_db.assert_awaited_once()
    infra.close_db.assert_awaited_once()


@pytest.mark.parametrize("entry", ["lazy", "eager", "direct"])
def test_non_signal_entries_use_the_same_bounded_lazy_initialization(
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = module.CeleryAsyncRuntime()

    result = runtime.run_async(lambda: asyncio.sleep(0, result=entry))

    assert result == entry
    assert _state_value(runtime) == "READY"
    infra.init_db.assert_awaited_once()
    runtime.shutdown()


def test_worker_init_is_logger_only_and_worker_process_signal_initializes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    from src.celery_app import app as app_module

    runtime = module.CeleryAsyncRuntime()
    monkeypatch.setattr(app_module, "celery_async_runtime", runtime, raising=False)
    setup_logger = MagicMock()
    monkeypatch.setattr(app_module, "setup_logger", setup_logger)

    app_module.on_worker_init()
    assert _state_value(runtime) == "NEW"
    app_module.on_worker_process_init()
    assert _state_value(runtime) == "READY"
    setup_logger.assert_called()
    runtime.shutdown()


def test_runtime_rejects_fork_inherited_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = module.CeleryAsyncRuntime()
    runtime.initialize()
    owner_pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: owner_pid + 1)

    with pytest.raises(RuntimeError, match=r"(?i)(owner|pid|fork)"):
        runtime.run_async(lambda: asyncio.sleep(0))


def test_each_message_runs_with_a_fresh_context(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = module.CeleryAsyncRuntime()
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
    runtime = module.CeleryAsyncRuntime()
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


def test_initialization_exception_cleans_partial_resources_and_returns_to_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    infra.init_db.side_effect = ConnectionError("database unavailable")
    runtime = module.CeleryAsyncRuntime()

    with pytest.raises(ConnectionError, match="database unavailable"):
        runtime.initialize()

    assert _state_value(runtime) == "NEW"
    assert runtime.runner is None
    infra.close_db.assert_awaited()


@pytest.mark.parametrize("timed_out_stage", ["pending", "redis", "database"])
def test_shutdown_timeout_continues_all_remaining_stages(
    monkeypatch: pytest.MonkeyPatch,
    timed_out_stage: str,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    runtime = module.CeleryAsyncRuntime()
    runtime.initialize()
    pending_cleanup = AsyncMock()
    monkeypatch.setattr(runtime, "_cancel_pending_tasks", pending_cleanup, raising=False)
    stages = {
        "pending": pending_cleanup,
        "redis": infra.close_redis,
        "database": infra.close_db,
    }
    stages[timed_out_stage].side_effect = TimeoutError(f"{timed_out_stage} timeout")

    runtime.shutdown()

    pending_cleanup.assert_awaited_once()
    infra.close_redis.assert_awaited_once()
    infra.close_db.assert_awaited_once()
    assert _state_value(runtime) == "CLOSED"


def test_normal_shutdown_has_no_default_executor_and_closes_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runtime_module()
    _patch_infrastructure(monkeypatch, module)
    runtime = module.CeleryAsyncRuntime()
    runtime.initialize()
    runner = runtime.runner
    loop = runner.get_loop()
    close_spy = MagicMock(wraps=runner.close)
    monkeypatch.setattr(runner, "close", close_spy)

    runtime.run_async(lambda: asyncio.sleep(0))
    assert getattr(loop, "_default_executor", None) is None
    runtime.shutdown()

    close_spy.assert_called_once()
    assert loop.is_closed()


class _ObservedMonotonicClock:
    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.observations: list[float] = []

    def __call__(self) -> float:
        current = time.monotonic()
        self.observations.append(current)
        return current


@pytest.mark.parametrize("hanging_stage", ["redis_ping", "redis_cleanup"])
def test_worker_process_init_hanging_redis_stage_shares_three_second_deadline(
    monkeypatch: pytest.MonkeyPatch,
    hanging_stage: str,
) -> None:
    module = _runtime_module()
    infra = _patch_infrastructure(monkeypatch, module)
    from src.celery_app import app as app_module

    never = asyncio.Event()

    async def hang() -> None:
        await never.wait()

    if hanging_stage == "redis_ping":
        infra.init_redis.side_effect = hang
    else:
        infra.init_redis.side_effect = ConnectionError("redis ping failed")
        infra.close_redis.side_effect = hang
    clock = _ObservedMonotonicClock()
    runtime = module.CeleryAsyncRuntime(monotonic=clock)
    monkeypatch.setattr(app_module, "celery_async_runtime", runtime, raising=False)

    started_at = time.monotonic()
    app_module.on_worker_process_init()
    elapsed = time.monotonic() - started_at

    assert elapsed <= 3.10, "Redis ping/cleanup 必须共享 worker_process_init 的 3 秒整体 deadline"
    assert clock.observations
    assert max(clock.observations) - clock.observations[0] <= 3.10
    assert _state_value(runtime) == "READY"
    assert runtime.degraded_redis is True
    runtime.shutdown()
