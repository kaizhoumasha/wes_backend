"""唯一日志写入、容量/预算/取消、后台冻结和摘要 SQL 的 FAST owner。"""

import asyncio
import importlib
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks

from src.app.sys.models.api_access_log import APIAccessLogCreate, APIAccessLogSummary
from src.app.sys.repositories.api_access_log_repository import APIAccessLogRepository
from src.app.sys.services.api_access_log_service import APIAccessLogService, api_access_log_background_scope
from src.core.conf import settings
from src.core.query_models import QueryOptions


def _entry(**kwargs):
    return APIAccessLogCreate(system_id="ecs", direction="INBOUND", method="POST", path="/event", **kwargs)


async def test_record_uses_one_base_create_and_one_commit_in_independent_session():
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    row = object()
    repo = APIAccessLogRepository()
    repo.create = AsyncMock(return_value=row)

    @asynccontextmanager
    async def sessions():
        yield db

    service = APIAccessLogService(repository=repo, session_context=sessions)
    assert await service.record(_entry()) is row
    repo.create.assert_awaited_once_with(db, _entry().model_dump())
    db.commit.assert_awaited_once()


async def test_record_database_failure_propagates_and_base_commit_rolls_back():
    db = SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("commit")), rollback=AsyncMock())
    repo = APIAccessLogRepository()
    repo.create = AsyncMock(return_value=object())

    @asynccontextmanager
    async def sessions():
        yield db

    with pytest.raises(RuntimeError, match="commit"):
        await APIAccessLogService(repository=repo, session_context=sessions).record(_entry())
    db.rollback.assert_awaited_once()


async def test_try_record_skips_capacity_without_waiting_and_releases_after_timeout(monkeypatch):
    monkeypatch.setattr(settings, "API_ACCESS_LOG_MAX_CONCURRENT_WRITES", 1)
    monkeypatch.setattr(settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", 10)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked(entry):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    service = APIAccessLogService()
    service.record = AsyncMock(side_effect=blocked)
    first = asyncio.create_task(service.try_record(_entry()))
    await entered.wait()
    await service.try_record(_entry())
    assert service.record.await_count == 1
    await first
    assert cleaned.is_set()
    service.record = AsyncMock()
    await service.try_record(_entry())
    service.record.assert_awaited_once()


async def test_try_record_isolates_failure_but_propagates_external_cancellation():
    service = APIAccessLogService()
    service.record = AsyncMock(side_effect=RuntimeError("database"))
    await service.try_record(_entry())
    service.record = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await service.try_record(_entry())
    service.record = AsyncMock()
    await service.try_record(_entry())
    service.record.assert_awaited_once()


@pytest.mark.parametrize(
    ("write_timeout_ms", "cancel_delay", "repeated_cancel"),
    [(100, 0.08, False), (100, 0.08, True), (20, 0.04, False)],
    ids=["external_first", "external_repeated", "timeout_then_external_during_cleanup"],
)
async def test_external_cancel_stops_the_write_timer_before_independent_session_cleanup(
    monkeypatch, write_timeout_ms, cancel_delay, repeated_cancel
):
    module = importlib.import_module("src.app.sys.services.api_access_log_service")
    monkeypatch.setattr(settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", write_timeout_ms)
    entered = asyncio.Event()
    events = []

    async def blocked(*_args):
        entered.set()
        await asyncio.Event().wait()

    async def rollback():
        assert module._active_writes == 1
        await asyncio.sleep(0.04)
        events.append("rollback_finished")

    async def close():
        assert module._active_writes == 1
        await asyncio.sleep(0.04)
        events.append("close_finished")

    db = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock(side_effect=rollback), close=AsyncMock(side_effect=close)
    )
    repo = APIAccessLogRepository()
    repo.create = AsyncMock(side_effect=blocked)

    @asynccontextmanager
    async def sessions():
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise
        finally:
            await db.close()

    service = APIAccessLogService(repository=repo, session_context=sessions)
    call = asyncio.create_task(service.try_record(_entry()))
    await entered.wait()
    asyncio.get_running_loop().call_later(cancel_delay, call.cancel, "first")
    if repeated_cancel:
        asyncio.get_running_loop().call_later(0.095, call.cancel, "second")
    with pytest.raises(asyncio.CancelledError) as caught:
        await call
    assert caught.value.args == ("second" if repeated_cancel else "first",)
    assert events == ["rollback_finished", "close_finished"]
    assert module._active_writes == 0
    service.record = AsyncMock()
    await service.try_record(_entry())
    service.record.assert_awaited_once()


async def test_defer_record_freezes_details_and_only_executes_in_host_scope(monkeypatch):
    service = APIAccessLogService()
    service.try_record = AsyncMock()
    monkeypatch.setattr(
        importlib.import_module("src.app.sys.services.api_access_log_service"), "get_background_tasks", lambda: None
    )
    source = _entry(details={"items": [1]})
    service.defer_record(source)
    service.try_record.assert_not_called()
    with api_access_log_background_scope() as tasks:
        assert isinstance(tasks, BackgroundTasks)
        service.defer_record(source)
        source.details["items"].append(2)
        service.try_record.assert_not_called()
        await tasks()
        assert service.try_record.await_args.args[0].details == {"items": [1]}
    assert tasks.tasks == []
    service.defer_record(source)
    assert service.try_record.await_count == 1


async def test_summary_query_counts_once_and_selects_only_summary_columns():
    repo = APIAccessLogRepository()
    repo.count = AsyncMock(return_value=4)
    row = {
        "id": 3,
        "system_id": "wms",
        "direction": "INBOUND",
        "method": "POST",
        "path": "/events",
        "created_at": datetime(2026, 10, 9),
    }
    result = Mock()
    result.mappings.return_value.all.return_value = [row]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    total, rows = await repo.query_summary(db, QueryOptions(limit=20))
    assert total == 4
    assert rows == [row]
    repo.count.assert_awaited_once()
    assert {column.name for column in db.execute.await_args.args[0].selected_columns} == set(
        APIAccessLogSummary.model_fields
    )
    assert "details" not in str(db.execute.await_args.args[0])
    db.execute.assert_awaited_once()


async def test_cleanup_uses_independent_transaction_and_bounded_strict_cutoff(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repo = APIAccessLogRepository()
    repo.delete_expired_batch = AsyncMock(return_value=5000)

    @asynccontextmanager
    async def sessions():
        yield db

    assert await APIAccessLogService(repository=repo, session_context=sessions).cleanup() == 5000
    assert repo.delete_expired_batch.await_args.kwargs["limit"] == 5000
    db.commit.assert_awaited_once()
    repo.delete_expired_batch = AsyncMock(side_effect=RuntimeError("delete"))
    with pytest.raises(RuntimeError):
        await APIAccessLogService(repository=repo, session_context=sessions).cleanup()
    db.rollback.assert_awaited_once()
