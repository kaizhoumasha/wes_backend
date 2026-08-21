"""Transport broker heavy harness 的隔离与故障清理回归。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import psutil
import pytest

from tests.support import transport_broker as harness

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+asyncpg://user:password@127.0.0.1:5432/test_transport"
REDIS_URL = "redis://127.0.0.1:6379/15"


def test_worker_readiness_budget_covers_a_cold_ci_container_start() -> None:
    assert harness.WORKER_READY_TIMEOUT_SECONDS >= 60


def _flatten_errors(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [nested for child in error.exceptions for nested in _flatten_errors(child)]
    return [error]


@pytest.mark.parametrize(
    "redis_url",
    (
        "redis://127.0.0.1:6379/0",
        "redis://redis.example.com:6379/15",
    ),
)
def test_worker_rejects_non_isolated_or_non_local_redis(redis_url: str) -> None:
    with pytest.raises(AssertionError, match="local/build-scoped non-zero test database"):
        harness.TransportBrokerWorker(DATABASE_URL, redis_url, Path("provider.yaml"))


def test_worker_accepts_build_scoped_compose_redis_on_non_zero_database() -> None:
    worker = harness.TransportBrokerWorker(
        DATABASE_URL,
        "redis://redis:6379/15",
        Path("provider.yaml"),
        run_id="compose-network-proof",
    )

    try:
        assert worker.key_prefix == "it:transport:compose-network-proof:"
    finally:
        worker.producer.close()


def test_worker_applies_one_run_prefix_to_broker_and_result_backend() -> None:
    worker = harness.TransportBrokerWorker(DATABASE_URL, REDIS_URL, Path("provider.yaml"), run_id="prefix-proof")

    assert getattr(worker, "key_prefix", None) == "it:transport:prefix-proof:"
    assert dict(worker.producer.conf.broker_transport_options)["global_keyprefix"] == worker.key_prefix
    assert dict(worker.producer.conf.result_backend_transport_options)["global_keyprefix"] == worker.key_prefix
    worker.producer.close()


def test_worker_close_kills_surviving_process_group_and_attempts_every_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = harness.TransportBrokerWorker(DATABASE_URL, REDIS_URL, Path("provider.yaml"))

    class FakeProcess:
        pid = 43221

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def terminate() -> None:
            cleanup_calls.append("leader-terminate")

        @staticmethod
        def wait(timeout: float) -> int:
            return 0

    class FakeChild:
        pid = 43222

    class FakeProducer:
        def close(self) -> None:
            cleanup_calls.append("producer")

    cleanup_calls: list[str] = []
    wait_calls = 0
    worker.process = cast("Any", FakeProcess())
    worker.log_path = tmp_path / "retained-transport-worker.log"
    worker._log_file = worker.log_path.open("w+")
    worker._descendant_pids = {FakeChild.pid}
    worker.producer = cast("Any", FakeProducer())
    monkeypatch.setattr(worker, "_capture_descendants", lambda: None, raising=False)
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
        "_cleanup_broker_artifacts",
        lambda: (_ for _ in ()).throw(RuntimeError("injected redis cleanup failure")),
        raising=False,
    )
    monkeypatch.setattr(
        worker,
        "_wait_for_connection_drain",
        lambda: (_ for _ in ()).throw(RuntimeError("injected database drain failure")),
        raising=False,
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        worker.close(success=True)

    assert any(call.startswith("killpg:43221:") for call in cleanup_calls)
    assert "producer" in cleanup_calls
    cleanup_errors = [str(error) for error in _flatten_errors(exc_info.value)]
    assert any("redis cleanup failure" in error for error in cleanup_errors)
    assert any("database drain failure" in error for error in cleanup_errors)
    assert worker.process is None
    assert worker._log_file.closed
    assert worker.log_path.exists()


@pytest.mark.asyncio
async def test_resource_cleanup_attempts_worker_runtime_http_redis_and_database_and_groups_failures() -> None:
    cleanup = getattr(harness, "close_transport_test_resources", None)
    assert cleanup is not None
    calls: list[str] = []

    class FakeWorker:
        def close(self, *, success: bool) -> None:
            calls.append(f"worker:{success}")
            raise RuntimeError("worker cleanup failure")

    class FakeRuntime:
        async def aclose(self) -> None:
            calls.append("runtime")
            raise RuntimeError("runtime cleanup failure")

    class FakeServer:
        def close(self) -> None:
            calls.append("http")
            raise RuntimeError("http cleanup failure")

    async def cleanup_database() -> None:
        calls.append("database")
        raise RuntimeError("database cleanup failure")

    primary = AssertionError("scenario failure")
    with pytest.raises(BaseExceptionGroup) as exc_info:
        await cleanup(
            worker=FakeWorker(),
            runtime=FakeRuntime(),
            server=FakeServer(),
            cleanup_database=cleanup_database,
            success=False,
            primary_error=primary,
        )

    assert calls == ["worker:False", "runtime", "http", "database"]
    errors = _flatten_errors(exc_info.value)
    assert primary in errors
    assert {str(error) for error in errors} >= {
        "worker cleanup failure",
        "runtime cleanup failure",
        "http cleanup failure",
        "database cleanup failure",
    }
