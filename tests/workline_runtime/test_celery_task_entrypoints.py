from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from src.app.workline.services.write_back_service import _map_command_task_type
from src.celery_app import config
from src.celery_app.tasks import workline as workline_tasks
from src.celery_app.tasks.workline import _ensure_non_empty_retry_result
from src.database import db as db_module
from src.workline_runtime.diagnostics import ErrorCode

if TYPE_CHECKING:
    from types import TracebackType

from src.app.workline.services.inbox_batch_processor import InboxBatchProcessor, _resolve_effect_source_device


class _MockScalars:
    def all(self) -> list[Any]:
        return []


class _MockResult:
    def scalars(self) -> _MockScalars:
        return _MockScalars()


class _FakeAsyncSession:
    def __init__(self, commit_success: bool = True) -> None:
        self.commit_success = commit_success
        self.committed = False
        self.closed = False

    async def __aenter__(self) -> _FakeAsyncSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _ = exc_type, exc, tb

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        self.closed = True

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return _MockResult()


def test_ensure_non_empty_retry_result_allows_empty_first_attempt() -> None:
    _ensure_non_empty_retry_result(
        "process_inbox_batch",
        {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        },
        retries=0,
    )


def test_ensure_non_empty_retry_result_allows_non_empty_retry() -> None:
    _ensure_non_empty_retry_result(
        "scan_timeouts_batch",
        {
            "scanned": 1,
            "timeouts_created": 0,
            "ack_timeouts_reconciled": 0,
            "errors": 0,
        },
        retries=2,
    )


def test_ensure_non_empty_retry_result_rejects_empty_retry() -> None:
    with pytest.raises(RuntimeError, match="process_inbox_batch returned an empty result after 2 retries"):
        _ensure_non_empty_retry_result(
            "process_inbox_batch",
            {
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            },
            retries=2,
        )


@pytest.mark.parametrize("task_type", ["MEASUREMENT_REEL", "MOVE_FORWARD", "PICK_AND_PUT"])
def test_map_command_task_type_preserves_plugin_task_type(task_type: str) -> None:
    assert _map_command_task_type(task_type) == task_type


def test_smt_full_box_exchange_candidate_scan_task_is_removed() -> None:
    assert not hasattr(workline_tasks, "scan_smt_full_box_exchange_candidates_batch")
    assert "scan-smt-full-box-exchange-candidates-batch" not in config.beat_schedule


def test_smt_inbound_handoff_recovery_replaces_old_full_box_scan_task() -> None:
    assert hasattr(workline_tasks, "scan_smt_inbound_handoff_demands_batch")
    assert "scan-smt-inbound-handoff-demands-batch" in config.beat_schedule


def test_system_outbox_dispatch_task_is_registered() -> None:
    from src.celery_app.app import celery_app
    from src.celery_app.tasks import sys as sys_tasks

    assert hasattr(sys_tasks, "dispatch_system_outbox_batch")
    assert config.beat_schedule["dispatch-outbox-batch"]["task"] == (
        "src.celery_app.tasks.sys.dispatch_system_outbox_batch"
    )
    assert config.task_routes["src.celery_app.tasks.sys.*"]["queue"] == "celery"
    assert "src.celery_app.tasks.sys" in celery_app.conf.include


def test_legacy_outbox_dispatch_task_names_are_removed() -> None:
    from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
    from src.celery_app.app import celery_app
    from src.core.task_queue_gateway import DISPATCH_SYSTEM_OUTBOX_TASK

    sent_tasks: list[str] = []

    class FakeQueueGateway:
        def enqueue_workline_inbox(self, *, limit: int = 10) -> None:
            _ = limit

        def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None:
            _ = outbox_id, limit
            sent_tasks.append(DISPATCH_SYSTEM_OUTBOX_TASK)

        def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None:
            _ = target_code, payload

    CallbackOrchestrationService(queue_gateway=FakeQueueGateway())._enqueue_outbox_dispatch()

    assert sent_tasks == ["src.celery_app.tasks.sys.dispatch_system_outbox_batch"]
    assert not hasattr(workline_tasks, "dispatch_outbox_batch")
    assert "src.celery_app.tasks.workline.dispatch_outbox_batch" not in celery_app.tasks
    assert "src.celery_app.tasks.handling.dispatch_system_outbox_batch" not in celery_app.tasks


def test_celery_facade_contracts() -> None:
    from src.celery_app.app import celery_app

    assert hasattr(workline_tasks, "process_inbox_batch")
    assert "src.celery_app.tasks.workline.process_inbox_batch" in celery_app.tasks

    assert not hasattr(workline_tasks, "process_inbox_messages")
    assert not hasattr(workline_tasks, "ProcessInboxMessages")
    assert not hasattr(workline_tasks, "OutboxDispatcher")


def test_resolve_effect_source_device_uses_rack_operation_resume_code_from_context() -> None:
    conveyor = cast("Any", type("Device", (), {"device_code": "PIPELINE02", "device_role": "CONVEYOR"})())
    session = cast(
        "Any",
        type(
            "Session",
            (),
            {
                "context_json": {
                    "rack_operation": {
                        "resume_source_device_code": "PIPELINE02",
                        "resume_source_device_role": "STALE_ROLE",
                    }
                }
            },
        )(),
    )
    inbox = cast("Any", type("Inbox", (), {"payload_json": {"callback_type": "WMS_RACK_ARRIVED"}})())

    assert _resolve_effect_source_device(inbox, session, {"CONVEYOR": [conveyor]}) is conveyor


def test_resolve_effect_source_device_uses_rack_operation_resume_code_for_conveyor() -> None:
    conveyor = cast("Any", type("Device", (), {"device_code": "PIPELINE01", "device_role": "CONVEYOR"})())
    session = cast(
        "Any",
        type(
            "Session",
            (),
            {
                "context_json": {
                    "rack_operation": {
                        "resume_source_device_code": "PIPELINE01",
                        "resume_source_device_role": "CONVEYOR",
                    }
                }
            },
        )(),
    )
    inbox = cast("Any", type("Inbox", (), {"payload_json": {"callback_type": "WMS_RACK_ARRIVED"}})())

    assert _resolve_effect_source_device(inbox, session, {"CONVEYOR": [conveyor]}) is conveyor


def test_workline_task_direct_call_lazy_initializes_db(monkeypatch: pytest.MonkeyPatch) -> None:
    task = workline_tasks.process_inbox_batch
    task.cleanup()
    init_called = False

    def fake_session_factory() -> _FakeAsyncSession:
        return _FakeAsyncSession()

    async def fake_init_db() -> None:
        nonlocal init_called
        init_called = True
        cast("Any", db_module).AsyncSessionLocal = fake_session_factory

    async def fake_process_batch(self: Any, db: Any, limit: int = 10) -> workline_tasks.ProcessResult:
        assert isinstance(db, _FakeAsyncSession)
        assert limit == 0
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "resource_wait": 0}

    monkeypatch.setattr(db_module, "AsyncSessionLocal", None)
    monkeypatch.setattr(db_module, "init_db", fake_init_db)

    # ProcessInboxMessages is removed, now we should mock the new inbox_batch_processor
    monkeypatch.setattr(InboxBatchProcessor, "process_batch", fake_process_batch)

    try:
        assert task(limit=0) == {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "resource_wait": 0}
        assert init_called
    finally:
        task.cleanup()


def test_process_inbox_batch_records_batch_diagnostic_on_final_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    task = workline_tasks.process_inbox_batch
    task.cleanup()
    recorded: list[dict[str, Any]] = []
    retry_calls: list[dict[str, Any]] = []

    class RetryScheduled(RuntimeError):
        pass

    def fake_session_factory() -> _FakeAsyncSession:
        return _FakeAsyncSession()

    async def failing_process_batch(self: Any, db: Any, limit: int = 10) -> workline_tasks.ProcessResult:
        _ = self, db, limit
        raise RuntimeError("batch-db-down")

    async def fake_record_diagnostic(db: Any, **kwargs: Any) -> None:
        recorded.append({"db": db, **kwargs})

    def fake_retry(*, exc: Exception, countdown: int) -> None:
        retry_calls.append({"exc": exc, "countdown": countdown})
        raise RetryScheduled(str(exc))

    monkeypatch.setattr(db_module, "AsyncSessionLocal", fake_session_factory)
    monkeypatch.setattr(InboxBatchProcessor, "process_batch", failing_process_batch)
    monkeypatch.setattr("src.app.workline.diagnostic_support._record_diagnostic", fake_record_diagnostic)
    monkeypatch.setattr(task, "retry", fake_retry)
    task.request.retries = task.max_retries
    task.request.id = "task-123"

    try:
        with pytest.raises(RetryScheduled, match="batch-db-down"):
            task(limit=7)
    finally:
        task.request.retries = 0
        task.request.id = None
        task.cleanup()

    assert len(retry_calls) == 1
    assert isinstance(retry_calls[0]["exc"], RuntimeError)
    assert retry_calls[0]["countdown"] == 40
    assert recorded
    diagnostic = recorded[0]
    assert diagnostic["inbox"] is None
    assert diagnostic["error_code"] == ErrorCode.INBOX_RETRY_EXHAUSTED
    assert diagnostic["message"] == "Inbox batch processing exhausted retries: batch-db-down"
    assert diagnostic["request_id"] == "task-123"
    assert diagnostic["trace_id"] == "celery:src.celery_app.tasks.workline.process_inbox_batch"
    assert diagnostic["extra"]["task_name"] == "src.celery_app.tasks.workline.process_inbox_batch"
    assert diagnostic["extra"]["limit"] == 7
    assert diagnostic["extra"]["retries"] == task.max_retries
    assert diagnostic["extra"]["exception_type"] == "RuntimeError"
