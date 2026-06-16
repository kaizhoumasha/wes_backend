"""SMT inbound handoff recovery Celery task tests."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from src.celery_app import config
from src.celery_app.app import celery_app
from src.celery_app.tasks import workline as workline_tasks


def test_smt_inbound_handoff_recovery_task_is_registered() -> None:
    task_name = "src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch"

    assert hasattr(workline_tasks, "scan_smt_inbound_handoff_demands_batch")
    assert task_name in celery_app.tasks
    assert config.beat_schedule["scan-smt-inbound-handoff-demands-batch"]["task"] == task_name
    assert config.task_routes["src.celery_app.tasks.workline.*"]["queue"] == "celery"


def test_smt_inbound_handoff_recovery_task_summary_contract() -> None:
    empty_summary = workline_tasks._empty_smt_inbound_handoff_recovery_result()

    assert empty_summary == {
        "scanned": 0,
        "claimed": 0,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }


def test_smt_inbound_handoff_recovery_task_legacy_limit_keyword_uses_old_scan_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = {
        "scanned": 0,
        "claimed": 0,
        "advanced": 0,
        "retry_scheduled": 0,
        "manual_hold": 0,
        "recovery_errors": 0,
    }

    class _FakeService:
        async def scan_smt_inbound_handoff_demands_batch(self, db: object, **kwargs: object) -> dict[str, int]:
            captured["db"] = db
            captured["kwargs"] = kwargs
            return expected

    class _FakeDbContext:
        async def __aenter__(self) -> object:
            return "db-session"

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def close(self) -> None:
            return None

    task = workline_tasks.scan_smt_inbound_handoff_demands_batch
    task.cleanup()
    task._db = _FakeDbContext()
    task.request.retries = 0

    monkeypatch.setattr(
        workline_tasks,
        "_run_async",
        lambda coro: workline_tasks._get_sync_event_loop().run_until_complete(coro),
    )
    handoff_service_module = importlib.import_module("src.app.workline.services.smt_inbound_handoff_service")
    monkeypatch.setattr(
        handoff_service_module,
        "smt_inbound_handoff_service",
        _FakeService(),
    )

    try:
        result = task(
            limit=7,
            stale_after_seconds=45,
        )
    finally:
        task.cleanup()

    assert result == expected
    assert captured["kwargs"] == {
        "limit": 7,
        "stale_after_seconds": 45,
    }
