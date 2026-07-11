"""RuntimeInbox Celery 切换契约。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

RUNTIME_INBOX_TASK = "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch"
LEGACY_INBOX_TASK = "src.celery_app.tasks.workline.process_inbox_batch"


def _empty_result() -> dict[str, int]:
    return {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "resource_wait": 0}


class _SessionStub:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> _SessionStub:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_runtime_inbox_task_is_registered_and_routed() -> None:
    from src.celery_app.app import celery_app
    from src.celery_app.config import task_routes

    celery_app.loader.import_default_modules()
    assert RUNTIME_INBOX_TASK in celery_app.tasks
    assert LEGACY_INBOX_TASK not in celery_app.tasks
    assert task_routes["src.celery_app.tasks.runtime_inbox.*"] == {"queue": "celery"}


def test_beat_uses_runtime_inbox_as_ten_second_fallback() -> None:
    from src.celery_app.config import beat_schedule

    inbox_entries = [entry for entry in beat_schedule.values() if entry["task"] == RUNTIME_INBOX_TASK]

    assert inbox_entries == [{"task": RUNTIME_INBOX_TASK, "schedule": 10.0}]
    assert beat_schedule["process-runtime-inbox-batch"] == inbox_entries[0]
    assert "process-inbox-batch" not in beat_schedule
    assert all(entry["task"] != LEGACY_INBOX_TASK for entry in beat_schedule.values())


def test_gateway_enqueues_only_runtime_inbox_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.task_queue_gateway import CeleryTaskQueueGateway

    gateway = CeleryTaskQueueGateway()
    send_task = MagicMock()
    monkeypatch.setattr(gateway, "_send_task", send_task)

    gateway.enqueue_runtime_inbox(limit=7)

    send_task.assert_called_once_with(RUNTIME_INBOX_TASK, kwargs={"limit": 7})
    assert not hasattr(gateway, "enqueue_workline_inbox")


def test_gateway_does_not_fallback_when_runtime_task_is_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import NotRegistered

    from src.core.task_queue_gateway import CeleryTaskQueueGateway

    gateway = CeleryTaskQueueGateway()
    send_task = MagicMock(side_effect=NotRegistered(RUNTIME_INBOX_TASK))
    monkeypatch.setattr(gateway, "_send_task", send_task)

    with pytest.raises(NotRegistered):
        gateway.enqueue_runtime_inbox()

    send_task.assert_called_once_with(RUNTIME_INBOX_TASK, kwargs={"limit": 10})


def test_runtime_inbox_task_empty_batch_returns_minimum_sli(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import runtime_inbox_service
    from src.celery_app.tasks import runtime_inbox as task_module

    db = _SessionStub()
    monkeypatch.setattr(task_module.process_runtime_inbox_batch, "_db", db)
    monkeypatch.setattr(task_module, "_run_async", asyncio.run)
    monkeypatch.setattr(runtime_inbox_service, "claim_for_processing", AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime_inbox_service, "recover_stale_leases", AsyncMock(return_value=0))

    result = task_module.process_runtime_inbox_batch.run(limit=10)

    assert result == _empty_result()


def test_runtime_inbox_task_claims_and_processes_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import runtime_inbox_service
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge
    from src.celery_app.tasks import runtime_inbox as task_module

    db = _SessionStub()
    events: list[str] = []
    claims = [
        [{"id": 1, "processor_token": "token-1"}],
        [{"id": 2, "processor_token": "token-2"}],
    ]

    async def claim_one(*_args: object, **kwargs: object) -> list[dict[str, object]]:
        assert isinstance(kwargs["processor_token"], str)
        assert kwargs["processor_token"]
        events.append(f"claim-{len(events)}")
        return claims.pop(0)

    class ProcessorStub:
        async def process_claimed(self, _db: object, *, claim: dict[str, object]) -> dict[str, int]:
            events.append(f"process-{claim['id']}")
            await asyncio.sleep(0)
            return {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}

    monkeypatch.setattr(task_module.process_runtime_inbox_batch, "_db", db)
    monkeypatch.setattr(task_module, "_run_async", asyncio.run)
    monkeypatch.setattr(runtime_inbox_service, "claim_for_processing", claim_one)
    monkeypatch.setattr(runtime_inbox_service, "recover_stale_leases", AsyncMock(return_value=0))
    monkeypatch.setattr(runtime_inbox_orchestrator_bridge, "RuntimeInboxProcessorBridge", ProcessorStub)

    result = task_module.process_runtime_inbox_batch.run(limit=2)

    assert events == ["claim-0", "process-1", "claim-2", "process-2"]
    assert result == {"processed": 2, "success": 2, "failed": 0, "skipped": 0, "resource_wait": 0}


def test_runtime_inbox_task_times_out_only_the_claimed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import runtime_inbox_service
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge
    from src.app.workline import constants
    from src.celery_app.tasks import runtime_inbox as task_module

    db = _SessionStub()
    claim_committed = False

    async def commit() -> None:
        nonlocal claim_committed
        claim_committed = True

    db.commit.side_effect = commit

    async def claim_for_processing(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        if claim_committed:
            return []
        return [{"id": 1, "processor_token": "token-1"}]

    claim_mock = AsyncMock(side_effect=claim_for_processing)

    class SlowProcessorStub:
        async def process_claimed(self, _db: object, *, claim: dict[str, object]) -> dict[str, int]:
            _ = claim
            assert claim_committed is True
            await asyncio.sleep(0.05)
            return _empty_result()

    monkeypatch.setattr(task_module.process_runtime_inbox_batch, "_db", db)
    monkeypatch.setattr(task_module, "_run_async", asyncio.run)
    monkeypatch.setattr(constants, "INBOX_PROCESS_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(runtime_inbox_service, "claim_for_processing", claim_mock)
    monkeypatch.setattr(runtime_inbox_service, "recover_stale_leases", AsyncMock(return_value=0))
    monkeypatch.setattr(runtime_inbox_orchestrator_bridge, "RuntimeInboxProcessorBridge", SlowProcessorStub)

    result = task_module.process_runtime_inbox_batch.run(limit=2)

    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
    assert claim_mock.await_count == 2
    assert db.commit.await_count >= 2
    db.rollback.assert_awaited_once()


def test_runtime_inbox_task_retries_batch_infrastructure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.celery_app.tasks import runtime_inbox as task_module

    retry = MagicMock(side_effect=RuntimeError("retry requested"))

    def fail_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]
        raise ConnectionError("db unavailable")

    monkeypatch.setattr(task_module, "_run_async", fail_run)
    monkeypatch.setattr(task_module.process_runtime_inbox_batch, "retry", retry)

    task_module.process_runtime_inbox_batch.push_request(retries=1)
    try:
        with pytest.raises(RuntimeError, match="retry requested"):
            task_module.process_runtime_inbox_batch.run(limit=10)
    finally:
        task_module.process_runtime_inbox_batch.pop_request()

    assert isinstance(retry.call_args.kwargs["exc"], ConnectionError)
    assert retry.call_args.kwargs["countdown"] == 10


def test_runtime_inbox_task_rolls_back_processor_exception_after_claim_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import runtime_inbox_service
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge
    from src.celery_app.tasks import runtime_inbox as task_module

    db = _SessionStub()
    claim_committed = False

    async def commit() -> None:
        nonlocal claim_committed
        claim_committed = True

    class FailingProcessorStub:
        async def process_claimed(self, _db: object, *, claim: dict[str, object]) -> dict[str, int]:
            _ = claim
            assert claim_committed is True
            raise ConnectionError("processor database error")

    retry = MagicMock(side_effect=RuntimeError("retry requested"))
    db.commit.side_effect = commit
    monkeypatch.setattr(task_module.process_runtime_inbox_batch, "_db", db)
    monkeypatch.setattr(task_module, "_run_async", asyncio.run)
    monkeypatch.setattr(
        runtime_inbox_service,
        "claim_for_processing",
        AsyncMock(return_value=[{"id": 1, "processor_token": "token-1"}]),
    )
    monkeypatch.setattr(runtime_inbox_orchestrator_bridge, "RuntimeInboxProcessorBridge", FailingProcessorStub)
    monkeypatch.setattr(task_module.process_runtime_inbox_batch, "retry", retry)

    with pytest.raises(RuntimeError, match="retry requested"):
        task_module.process_runtime_inbox_batch.run(limit=1)

    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()
    assert isinstance(retry.call_args.kwargs["exc"], ConnectionError)
