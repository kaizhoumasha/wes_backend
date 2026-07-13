"""Celery prefork 集成测试 harness 的故障注入回归。"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any, cast

import psutil
import pytest

from tests.integration import test_celery_async_runtime_postgresql as harness

if TYPE_CHECKING:
    from pathlib import Path

    from celery import Celery  # pyright: ignore[reportMissingTypeStubs]

pytestmark = pytest.mark.integration

SERVICES = {
    "database": "test_prefork",
    "database_url": "postgresql+asyncpg://user:password@127.0.0.1:5432/test_prefork",
    "redis_url": "redis://127.0.0.1:6379/15",
}


def _flatten_errors(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [nested for child in error.exceptions for nested in _flatten_errors(child)]
    return [error]


def test_worker_start_failure_always_invokes_full_teardown_and_retains_log(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = harness.PreforkWorker(SERVICES)
    fake_process = type("FakeProcess", (), {"pid": 43210})()
    teardown_calls: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake_process)
    monkeypatch.setattr(
        harness,
        "_wait_until",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("injected readiness failure")),
    )
    monkeypatch.setattr(worker, "stop", lambda **kwargs: teardown_calls.append("stop"))

    with pytest.raises(AssertionError, match="injected readiness failure"):
        worker.start()

    assert teardown_calls == ["stop"]
    assert worker.log_path is not None and worker.log_path.exists()
    worker._log_file.close()
    worker.log_path.unlink(missing_ok=True)


def test_worker_stop_kills_surviving_process_group_and_aggregates_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = harness.PreforkWorker(SERVICES)

    class FakeProcess:
        pid = 43211

        @staticmethod
        def poll() -> int:
            return 0

    class FakeChild:
        pid = 43212

    class FakeProducer:
        def close(self) -> None:
            cleanup_calls.append("producer")

    cleanup_calls: list[str] = []
    wait_calls = 0
    worker.process = cast("subprocess.Popen[str]", FakeProcess())
    worker.log_path = tmp_path / "retained-worker.log"
    worker._log_file = worker.log_path.open("w+")
    worker._descendant_pids = {FakeChild.pid}
    worker._producer_app = cast("Celery", FakeProducer())
    monkeypatch.setattr(worker, "_capture_descendants", lambda: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", lambda pid: FakeChild())

    def fake_wait_procs(processes: list[Any], timeout: float) -> tuple[list[Any], list[Any]]:
        nonlocal wait_calls
        wait_calls += 1
        return ([], list(processes)) if wait_calls == 1 else (list(processes), [])

    monkeypatch.setattr(psutil, "wait_procs", fake_wait_procs)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: cleanup_calls.append(f"killpg:{pgid}:{sig}"))
    monkeypatch.setattr(
        worker,
        "_cleanup_redis",
        lambda: (_ for _ in ()).throw(RuntimeError("injected redis cleanup failure")),
    )
    monkeypatch.setattr(
        harness,
        "_wait_until",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected database drain failure")),
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        worker.stop(success=True)

    assert any(call.startswith("killpg:43211:") for call in cleanup_calls)
    assert "producer" in cleanup_calls
    cleanup_errors = [str(error) for error in _flatten_errors(exc_info.value)]
    assert any("redis cleanup failure" in error for error in cleanup_errors)
    assert any("database drain failure" in error for error in cleanup_errors)
    assert worker.process is None
    assert worker._log_file.closed
    assert worker.log_path.exists()


def test_quit_scenario_outer_finally_cleans_run_on_replacement_start_or_assertion_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cleanup_calls: list[str] = []
    retained_log = tmp_path / "first-worker.log"
    retained_log.write_text("diagnostic", encoding="utf-8")

    class FakeFirst:
        services = SERVICES
        run_id = "failure-injection"
        process = None

        @staticmethod
        def stop(**kwargs: object) -> None:
            cleanup_calls.append("first-stop")

        @staticmethod
        def cleanup_run_artifacts() -> None:
            cleanup_calls.append("cleanup")

    class FakeReplacement:
        process = None
        fail_on_start = False

        def __init__(self, services: dict[str, str], *, concurrency: int, run_id: str) -> None:
            assert services is SERVICES
            assert concurrency == 1
            assert run_id == "failure-injection"

        def start(self) -> FakeReplacement:
            if self.fail_on_start:
                raise AssertionError("injected replacement start failure")
            return self

        @staticmethod
        def stop(**kwargs: object) -> None:
            return None

    for fail_on_start in (True, False):
        FakeReplacement.fail_on_start = fail_on_start
        monkeypatch.setattr(harness, "PreforkWorker", FakeReplacement)
        with pytest.raises(AssertionError, match="injected"):
            first = cast("harness.PreforkWorker", FakeFirst())
            with harness._quit_scenario(first) as quit_state:
                replacement = harness.PreforkWorker(first.services, concurrency=1, run_id=first.run_id)
                quit_state["replacement"] = replacement
                replacement.start()
                raise AssertionError("injected post-start assertion failure")

    assert cleanup_calls == ["first-stop", "cleanup", "first-stop", "cleanup"]
    assert retained_log.read_text(encoding="utf-8") == "diagnostic"


def test_quit_scenario_outer_finally_cleans_before_replacement_failures(tmp_path: Path) -> None:
    retained_log = tmp_path / "first-worker-pre-replacement.log"
    retained_log.write_text("pre-replacement diagnostic", encoding="utf-8")

    class FakeFirst:
        services = SERVICES

        def __init__(self, calls: list[str], failure_phase: str) -> None:
            self.calls = calls
            self.run_id = f"pre-replacement-{failure_phase}"
            self.process: object | None = object()

        def stop(self, **kwargs: object) -> None:
            self.calls.append("stop")
            self.process = None

        def cleanup_run_artifacts(self) -> None:
            self.calls.append("cleanup")

    for failure_phase in ("wait", "after_stop"):
        calls: list[str] = []
        first = cast("harness.PreforkWorker", FakeFirst(calls, failure_phase))
        with pytest.raises(AssertionError, match="injected pre-replacement failure"):
            with harness._quit_scenario(first):
                if failure_phase == "after_stop":
                    first.stop(cleanup_redis=False)
                raise AssertionError("injected pre-replacement failure")

        assert calls[-1] == "cleanup"
        assert calls.count("cleanup") == 1
        assert retained_log.read_text(encoding="utf-8") == "pre-replacement diagnostic"
