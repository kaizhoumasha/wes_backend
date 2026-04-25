from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.outbox import OutboxStatus
from src.app.workline.repositories.outbox_repository import WorklineOutboxRepository


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _FakeDb:
    def __init__(self, value: object) -> None:
        self.value = value
        self.flush = AsyncMock()

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self.value)


@pytest.mark.asyncio
async def test_mark_as_sent_clears_retry_error_projection() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.DISPATCHING,
        sent_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_sent(db, 1)  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == OutboxStatus.SENT
    assert outbox.sent_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_acked_clears_retry_error_projection() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.SENT,
        finished_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_acked_by_dispatch_key(db, "device-command:CMD-1")  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == OutboxStatus.ACKED
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    db.flush.assert_awaited_once()
