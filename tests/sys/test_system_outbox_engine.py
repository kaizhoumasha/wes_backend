from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.services import SystemOutboxEngine as SystemOutboxDispatcher


class FakeSystemOutboxRepository:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages
        self.mark_dispatching_calls: list[int] = []
        self.mark_sent_calls: list[int] = []
        self.mark_failed_calls: list[tuple[int, str, int]] = []

    async def get_pending_messages(
        self,
        _db: Any,
        limit: int = 50,
        **_filters: Any,
    ) -> list[Any]:
        return self.messages[:limit]

    async def mark_as_dispatching(self, _db: Any, outbox_id: int) -> Any | None:
        self.mark_dispatching_calls.append(outbox_id)
        now = datetime(2026, 5, 22, 8, 0, 0)
        for message in self.messages:
            stale_dispatching = (
                message.status == SystemOutboxStatus.DISPATCHING
                and message.next_retry_at is not None
                and message.next_retry_at <= now
            )
            if message.id == outbox_id and (message.status == SystemOutboxStatus.NEW or stale_dispatching):
                message.status = SystemOutboxStatus.DISPATCHING
                message.next_retry_at = now + timedelta(minutes=5)
                return message
        return None

    async def mark_as_sent(self, _db: Any, outbox_id: int) -> Any | None:
        self.mark_sent_calls.append(outbox_id)
        for message in self.messages:
            if message.id == outbox_id and message.status == SystemOutboxStatus.DISPATCHING:
                message.status = SystemOutboxStatus.SENT
                return message
        return None

    async def mark_as_failed(self, _db: Any, outbox_id: int, error: str, max_retries: int = 3) -> Any | None:
        self.mark_failed_calls.append((outbox_id, error, max_retries))
        for message in self.messages:
            if message.id == outbox_id:
                message.status = SystemOutboxStatus.NEW
                message.last_error = error
                return message
        return None


async def _no_workline_messages(_db: Any, _limit: int) -> dict[str, int]:
    return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}


def _outbox(**overrides: Any) -> SimpleNamespace:
    values = {
        "id": 1,
        "dispatch_key": "handling:bin-operation:trace-001:move:1",
        "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP,
        "target_type": SystemOutboxTargetType.HTTP_ENDPOINT,
        "target_code": "WMS_RCS_BIN_OPERATION",
        "payload_json": {"operation_key": "bin-operation:trace-001"},
        "status": SystemOutboxStatus.NEW,
        "attempt_count": 0,
        "next_retry_at": None,
        "last_error": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_sends_external_http_and_marks_sent() -> None:
    message = _outbox()
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    sender.assert_awaited_once_with(
        "http://wms-rcs/api/wes/transport-request", {"operation_key": "bin-operation:trace-001"}
    )
    assert repo.mark_dispatching_calls == [1]
    assert repo.mark_sent_calls == [1]
    assert message.status == SystemOutboxStatus.SENT
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_marks_failed_when_external_http_fails() -> None:
    message = _outbox(id=2)
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=False)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert repo.mark_failed_calls == [(2, "Dispatch failed", 3)]
    assert message.status == SystemOutboxStatus.NEW
    assert message.last_error == "Dispatch failed"


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_reclaims_stale_dispatching_message() -> None:
    message = _outbox(
        id=3,
        status=SystemOutboxStatus.DISPATCHING,
        next_retry_at=datetime(2026, 5, 22, 7, 59, 0),
    )
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    sender.assert_awaited_once()
    assert repo.mark_dispatching_calls == [3]
    assert message.status == SystemOutboxStatus.SENT


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_delegates_workline_domain_to_workline_governance() -> None:
    repo = FakeSystemOutboxRepository([])
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_workline_dispatcher(_db: Any, limit: int = 50) -> dict[str, int]:
        assert _db is db
        assert limit == 5
        return {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=fake_workline_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    assert repo.mark_dispatching_calls == []


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_delegates_device_command_to_device_gateway() -> None:
    message = _outbox(id=4, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND)
    repo = FakeSystemOutboxRepository([message])
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_device_dispatcher(_db: Any, outbox: Any) -> bool:
        assert _db is db
        assert outbox is message
        return True

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=fake_device_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
