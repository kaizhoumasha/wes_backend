"""RuntimeInbox PostgreSQL benchmark 的异步资源收敛合同。"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

import pytest

from tests.load.runtime_inbox_postgresql_benchmark import _managed_engine, _run_benchmark, _run_workers_with_monitor


@dataclass(slots=True)
class _FakeEngine:
    dispose_count: int = 0

    async def dispose(self) -> None:
        self.dispose_count += 1


def test_worker_failure_cancels_and_awaits_peers_stops_monitor_and_preserves_first_error() -> None:
    async def scenario() -> None:
        primary_error = RuntimeError("first worker failure")
        blocked_started = asyncio.Event()
        blocked_settled = asyncio.Event()
        monitor_settled = asyncio.Event()
        done = asyncio.Event()

        async def failing_worker() -> None:
            await blocked_started.wait()
            raise primary_error

        async def blocked_worker() -> None:
            blocked_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                blocked_settled.set()

        async def monitor() -> None:
            try:
                await done.wait()
            finally:
                monitor_settled.set()

        with pytest.raises(RuntimeError) as exc_info:
            await _run_workers_with_monitor(
                (failing_worker(), blocked_worker()),
                monitor(),
                done=done,
            )

        assert exc_info.value is primary_error
        assert done.is_set()
        assert blocked_settled.is_set()
        assert monitor_settled.is_set()

    asyncio.run(scenario())


def test_worker_success_and_engine_context_preserve_result_and_dispose_once() -> None:
    async def scenario() -> None:
        completed: list[str] = []
        done = asyncio.Event()

        async def worker(name: str) -> None:
            completed.append(name)

        async def monitor() -> None:
            await done.wait()
            completed.append("monitor")

        await _run_workers_with_monitor(
            (worker("worker-1"), worker("worker-2")),
            monitor(),
            done=done,
        )
        assert completed == ["worker-1", "worker-2", "monitor"]

        success_engine = _FakeEngine()
        async with _managed_engine(success_engine):
            completed.append("success")
        assert success_engine.dispose_count == 1

        failure_engine = _FakeEngine()
        primary_error = LookupError("scenario failure")
        with pytest.raises(LookupError) as exc_info:
            async with _managed_engine(failure_engine):
                raise primary_error
        assert exc_info.value is primary_error
        assert failure_engine.dispose_count == 1

    asyncio.run(scenario())


def test_worker_finish_timestamp_is_captured_before_blocked_monitor_cleanup() -> None:
    async def scenario() -> None:
        done = asyncio.Event()
        clock_called = asyncio.Event()
        allow_monitor_cleanup = asyncio.Event()
        monitor_settled = asyncio.Event()

        async def worker() -> None:
            return

        async def monitor() -> None:
            await done.wait()
            await allow_monitor_cleanup.wait()
            monitor_settled.set()

        def clock() -> float:
            assert not monitor_settled.is_set()
            clock_called.set()
            return 42.0

        task = asyncio.create_task(
            _run_workers_with_monitor(
                (worker(),),
                monitor(),
                done=done,
                clock=clock,
            )
        )
        await clock_called.wait()
        assert not task.done()

        allow_monitor_cleanup.set()
        assert await task == 42.0
        assert monitor_settled.is_set()

        benchmark_source = inspect.getsource(_run_benchmark)
        assert "elapsed_seconds = workers_finished_at - started_at" in benchmark_source

    asyncio.run(scenario())
