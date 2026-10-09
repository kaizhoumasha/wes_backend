"""日志后置的 FAST 合同：单一 Runner、预算、清理和消息隔离。"""

import asyncio
import importlib
import os
import time
from contextvars import ContextVar

import pytest

from src.app.sys.models.api_access_log import APIAccessLogCreate
from src.app.sys.services.api_access_log_service import api_access_log_service
from src.celery_app.async_runtime import CeleryAsyncRuntime, RuntimeState
from src.utils.background_tasks import get_background_tasks

service_module = importlib.import_module("src.app.sys.services.api_access_log_service")
runtime_module = importlib.import_module("src.celery_app.async_runtime")


@pytest.fixture
def runtime():
    instance = CeleryAsyncRuntime()
    instance._runner = asyncio.Runner()
    instance._owner_pid = os.getpid()
    instance._state = RuntimeState.READY
    yield instance
    instance._runner.close()


def entry(label):
    return APIAccessLogCreate(system_id="wms", direction="OUTBOUND", method="POST", path=f"/{label}")


@pytest.mark.parametrize("outcome", ["success", "error", "factory_error", "cancel"])
def test_worker_postlude_preserves_business_outcome_and_isolates_next_message(runtime, monkeypatch, outcome):
    events = []
    loops = []
    context = ContextVar("message", default="unset")
    failure = ValueError("business error")
    cancellation = asyncio.CancelledError("business cancelled")

    async def record(value):
        assert get_background_tasks() is None
        assert context.get() == "first"
        loops.append(asyncio.get_running_loop())
        events.append(value.path)

    monkeypatch.setattr(api_access_log_service, "record", record)

    async def business():
        assert get_background_tasks() is None
        loops.append(asyncio.get_running_loop())
        events.append("business_finished")
        if outcome == "error":
            raise failure
        if outcome == "cancel":
            raise cancellation
        return "original"

    def factory():
        context.set("first")
        api_access_log_service.defer_record(entry("diagnostic"))
        if outcome == "factory_error":
            events.append("business_finished")
            raise failure
        return business()

    if outcome == "success":
        assert runtime.run_async(factory) == "original"
    else:
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else ValueError) as caught:
            runtime.run_async(factory)
        assert caught.value is (cancellation if outcome == "cancel" else failure)
    assert events == (["business_finished"] if outcome == "cancel" else ["business_finished", "/diagnostic"])
    assert len(set(loops)) <= 1
    assert runtime.run_async(lambda: asyncio.sleep(0, result=context.get())) == "unset"
    assert service_module._log_background_tasks.get() is None
    assert service_module._log_write_deadline.get() is None
    assert service_module._active_writes == 0


def test_worker_batch_budget_waits_for_rollback_and_drops_remaining(runtime, monkeypatch):
    events = []
    monkeypatch.setattr(runtime_module, "API_ACCESS_LOG_BATCH_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(service_module.settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", 1000)

    async def record(value):
        events.append(value.path)
        try:
            await asyncio.sleep(5)
        finally:
            await asyncio.sleep(0.02)
            events.append("rollback_finished")

    monkeypatch.setattr(api_access_log_service, "record", record)

    async def business():
        for index in range(5):
            api_access_log_service.defer_record(entry(str(index)))
        await asyncio.sleep(0.04)  # 业务耗时不计入后置预算。
        return "business-result"

    assert runtime.run_async(business) == "business-result"
    assert events == ["/0", "rollback_finished"]
    assert service_module._active_writes == 0
    assert runtime.run_async(lambda: asyncio.sleep(0, result=len(asyncio.all_tasks()))) == 1


def test_worker_external_cancel_awaits_active_write_cleanup(runtime, monkeypatch):
    events = []
    message_task = None

    async def record(_value):
        asyncio.get_running_loop().call_soon(message_task.cancel)
        try:
            await asyncio.sleep(5)
        finally:
            await asyncio.sleep(0.01)
            events.append("rollback_finished")

    monkeypatch.setattr(api_access_log_service, "record", record)

    async def business():
        nonlocal message_task
        message_task = asyncio.current_task()
        api_access_log_service.defer_record(entry("first"))
        api_access_log_service.defer_record(entry("dropped"))
        return "business"

    with pytest.raises(asyncio.CancelledError):
        runtime.run_async(business)
    assert events == ["rollback_finished"]
    assert service_module._active_writes == 0
    assert service_module._log_write_deadline.get() is None
    assert runtime.run_async(lambda: asyncio.sleep(0, result=get_background_tasks())) is None


def test_worker_ordinary_diagnostic_error_cannot_override_original_business_exception(runtime, monkeypatch):
    failure = ValueError("original business error")

    async def fail_diagnostic(_value):
        raise LookupError("diagnostic")

    monkeypatch.setattr(api_access_log_service, "try_record", fail_diagnostic)

    async def business():
        api_access_log_service.defer_record(entry("failed"))
        raise failure

    with pytest.raises(ValueError) as caught:
        runtime.run_async(business)
    assert caught.value is failure
    assert runtime.run_async(lambda: asyncio.sleep(0, result="next")) == "next"


def test_default_worker_batch_and_single_write_budgets_bound_slow_diagnostics(runtime, monkeypatch):
    started = []
    cleaned = []

    async def record(value):
        started.append(value.path)
        try:
            await asyncio.sleep(5)
        finally:
            cleaned.append(value.path)

    monkeypatch.setattr(api_access_log_service, "record", record)
    assert service_module.settings.API_ACCESS_LOG_WRITE_TIMEOUT_MS == 100
    assert runtime_module.API_ACCESS_LOG_BATCH_TIMEOUT_SECONDS == 1.0

    async def business():
        for index in range(15):
            api_access_log_service.defer_record(entry(str(index)))
        return "original"

    began = time.monotonic()
    assert runtime.run_async(business) == "original"
    elapsed = time.monotonic() - began
    assert 0.9 <= elapsed < 1.5
    assert 8 <= len(started) <= 11
    assert cleaned == started and service_module._active_writes == 0


@pytest.mark.parametrize(("batch_seconds", "single_ms"), [(0.04, 80), (0.08, 40)])
@pytest.mark.parametrize("business_error", [False, True])
def test_overlapping_deadlines_wait_for_rollback_and_close_before_releasing_capacity(
    runtime, monkeypatch, batch_seconds, single_ms, business_error
):
    events = []
    failure = ValueError("original business failure")
    monkeypatch.setattr(runtime_module, "API_ACCESS_LOG_BATCH_TIMEOUT_SECONDS", batch_seconds)
    monkeypatch.setattr(service_module.settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", single_ms)

    async def record(value):
        events.append(value.path)
        try:
            await asyncio.Event().wait()
        finally:
            events.append("rollback_started")
            assert service_module._active_writes == 1
            await asyncio.sleep(0.05)
            events.append("rollback_finished")
            await asyncio.sleep(0.05)
            assert service_module._active_writes == 1
            events.append("close_finished")

    monkeypatch.setattr(api_access_log_service, "record", record)

    async def business():
        api_access_log_service.defer_record(entry("first"))
        api_access_log_service.defer_record(entry("remaining"))
        if business_error:
            raise failure
        return "original-result"

    if business_error:
        with pytest.raises(ValueError) as caught:
            runtime.run_async(business)
        assert caught.value is failure
    else:
        assert runtime.run_async(business) == "original-result"
    assert events == ["/first", "rollback_started", "rollback_finished", "close_finished"]
    assert service_module._active_writes == 0
    assert runtime.run_async(lambda: asyncio.sleep(0, result=len(asyncio.all_tasks()))) == 1
    assert service_module._log_background_tasks.get() is None
    assert service_module._log_write_deadline.get() is None


def test_external_cancel_before_both_deadlines_cannot_interrupt_write_cleanup(runtime, monkeypatch):
    events = []
    message_task = None
    monkeypatch.setattr(runtime_module, "API_ACCESS_LOG_BATCH_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(service_module.settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", 60)

    async def record(_value):
        asyncio.get_running_loop().call_later(0.03, message_task.cancel)
        try:
            await asyncio.Event().wait()
        finally:
            assert service_module._active_writes == 1
            await asyncio.sleep(0.06)
            events.append("rollback_finished")
            await asyncio.sleep(0.06)
            assert service_module._active_writes == 1
            events.append("close_finished")

    monkeypatch.setattr(api_access_log_service, "record", record)

    async def business():
        nonlocal message_task
        message_task = asyncio.current_task()
        api_access_log_service.defer_record(entry("first"))
        api_access_log_service.defer_record(entry("remaining"))

    with pytest.raises(asyncio.CancelledError):
        runtime.run_async(business)
    assert events == ["rollback_finished", "close_finished"]
    assert service_module._active_writes == 0
    assert runtime.run_async(lambda: asyncio.sleep(0, result=len(asyncio.all_tasks()))) == 1
    assert service_module._log_background_tasks.get() is None
    assert service_module._log_write_deadline.get() is None
