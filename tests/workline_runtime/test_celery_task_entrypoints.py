from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from src.celery_app import config
from src.celery_app.tasks import workline as workline_tasks
from src.celery_app.tasks.workline import _ensure_non_empty_retry_result, _map_command_task_type
from src.database import db as db_module

if TYPE_CHECKING:
    from types import TracebackType


class _FakeAsyncSession:
    async def __aenter__(self) -> _FakeAsyncSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _ = exc_type, exc, tb

    async def close(self) -> None:
        pass


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


def test_smt_full_box_exchange_candidate_scan_task_is_registered() -> None:
    assert hasattr(workline_tasks, "scan_smt_full_box_exchange_candidates_batch")
    assert config.beat_schedule["scan-smt-full-box-exchange-candidates-batch"] == {
        "task": "src.celery_app.tasks.workline.scan_smt_full_box_exchange_candidates_batch",
        "schedule": 60.0,
    }


def test_resolve_effect_source_device_uses_rack_exchange_resume_code() -> None:
    conveyor = cast("Any", type("Device", (), {"device_code": "PIPELINE02", "device_role": "CONVEYOR"})())
    session = cast(
        "Any",
        type(
            "Session",
            (),
            {
                "context_json": {
                    "rack_exchange": {
                        "resume_source_device_code": "PIPELINE02",
                        "resume_source_device_role": "STALE_ROLE",
                    }
                }
            },
        )(),
    )
    inbox = cast("Any", type("Inbox", (), {"payload_json": {"callback_type": "WMS_RACK_ARRIVED"}})())

    assert workline_tasks._resolve_effect_source_device(inbox, session, {"CONVEYOR": [conveyor]}) is conveyor


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

    async def fake_process_batch(db: Any, *, limit: int) -> workline_tasks.ProcessResult:
        assert isinstance(db, _FakeAsyncSession)
        assert limit == 0
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    monkeypatch.setattr(db_module, "AsyncSessionLocal", None)
    monkeypatch.setattr(db_module, "init_db", fake_init_db)
    monkeypatch.setattr(workline_tasks.ProcessInboxMessages, "_process_batch", staticmethod(fake_process_batch))

    try:
        assert task(limit=0) == {"processed": 0, "success": 0, "failed": 0, "skipped": 0}
        assert init_called
    finally:
        task.cleanup()
