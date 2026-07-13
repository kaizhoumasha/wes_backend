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
    dispose_error: BaseException | None = None

    async def dispose(self) -> None:
        self.dispose_count += 1
        if self.dispose_error is not None:
            raise self.dispose_error


def test_worker_failure_cancels_and_awaits_peers_stops_monitor_and_preserves_first_error() -> None:
    async def scenario() -> None:
        primary_error = RuntimeError("first worker failure")
        blocked_started = asyncio.Event()
        blocked_settled = asyncio.Event()
        monitor_settled = asyncio.Event()
        done = asyncio.Event()
        ready = asyncio.Event()

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
                ready.set()
                await done.wait()
            finally:
                monitor_settled.set()

        with pytest.raises(RuntimeError) as exc_info:
            await _run_workers_with_monitor(
                (failing_worker(), blocked_worker()),
                monitor(),
                done=done,
                ready=ready,
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
        ready = asyncio.Event()

        async def worker(name: str) -> None:
            completed.append(name)

        async def monitor() -> None:
            ready.set()
            await done.wait()
            completed.append("monitor")

        await _run_workers_with_monitor(
            (worker("worker-1"), worker("worker-2")),
            monitor(),
            done=done,
            ready=ready,
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
        ready = asyncio.Event()
        clock_called = asyncio.Event()
        allow_monitor_cleanup = asyncio.Event()
        monitor_settled = asyncio.Event()

        async def worker() -> None:
            return

        async def monitor() -> None:
            ready.set()
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
                ready=ready,
                clock=clock,
            )
        )
        await clock_called.wait()
        assert not task.done()

        allow_monitor_cleanup.set()
        assert await task == (42.0, 42.0)
        assert monitor_settled.is_set()

        benchmark_source = inspect.getsource(_run_benchmark)
        assert "elapsed_seconds = workers_finished_at - workers_started_at" in benchmark_source

    asyncio.run(scenario())


def test_workers_wait_for_first_monitor_observation_and_timing_starts_after_ready() -> None:
    async def scenario() -> None:
        allow_first_observation = asyncio.Event()
        worker_started = asyncio.Event()
        done = asyncio.Event()
        ready = asyncio.Event()
        observation_count = 0
        clock_values = iter((10.0, 12.0))

        async def worker() -> None:
            assert observation_count == 1
            worker_started.set()

        async def monitor() -> None:
            nonlocal observation_count
            await allow_first_observation.wait()
            observation_count += 1
            ready.set()
            await done.wait()

        task = asyncio.create_task(
            _run_workers_with_monitor(
                (worker(),),
                monitor(),
                done=done,
                ready=ready,
                clock=lambda: next(clock_values),
            )
        )
        await asyncio.sleep(0)
        assert worker_started.is_set() is False

        allow_first_observation.set()

        assert await task == (10.0, 12.0)
        assert observation_count == 1
        assert worker_started.is_set()

    asyncio.run(scenario())


def test_monitor_failure_before_ready_does_not_start_workers() -> None:
    async def scenario() -> None:
        worker_started = False
        done = asyncio.Event()
        ready = asyncio.Event()

        async def worker() -> None:
            nonlocal worker_started
            worker_started = True

        async def monitor() -> None:
            raise RuntimeError("monitor query failed")

        with pytest.raises(RuntimeError, match="monitor query failed"):
            await _run_workers_with_monitor(
                (worker(),),
                monitor(),
                done=done,
                ready=ready,
            )

        assert worker_started is False

    asyncio.run(scenario())


def test_engine_dispose_failure_preserves_body_and_cancel_primary_errors() -> None:
    async def scenario() -> None:
        body_error = LookupError("body primary")
        body_engine = _FakeEngine(dispose_error=RuntimeError("top-secret dispose detail"))
        with pytest.raises(LookupError) as body_error_info:
            async with _managed_engine(body_engine):
                raise body_error
        assert body_error_info.value is body_error
        assert body_engine.dispose_count == 1
        assert any(note == "cleanup=engine_dispose_failed" for note in getattr(body_error_info.value, "__notes__", ()))
        assert "top-secret" not in str(body_error_info.value)

        cancel_error = asyncio.CancelledError("cancel primary")
        cancel_engine = _FakeEngine(dispose_error=RuntimeError("top-secret dispose detail"))
        with pytest.raises(asyncio.CancelledError) as cancel_error_info:
            async with _managed_engine(cancel_engine):
                raise cancel_error
        assert cancel_error_info.value is cancel_error
        assert cancel_engine.dispose_count == 1
        assert any(
            note == "cleanup=engine_dispose_failed" for note in getattr(cancel_error_info.value, "__notes__", ())
        )

    asyncio.run(scenario())


def test_engine_dispose_failure_without_body_error_is_explicit() -> None:
    async def scenario() -> None:
        dispose_error = RuntimeError("dispose failure")
        engine = _FakeEngine(dispose_error=dispose_error)
        with pytest.raises(RuntimeError) as dispose_error_info:
            async with _managed_engine(engine):
                pass
        assert dispose_error_info.value is dispose_error
        assert engine.dispose_count == 1

    asyncio.run(scenario())
