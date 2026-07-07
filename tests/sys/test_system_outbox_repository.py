from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.sys.models import SystemOutboxStatus
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository


class _FakeResult:
    def __init__(self, outboxes: list[Any]) -> None:
        self._outboxes = outboxes

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._outboxes


class _CapturingDb:
    def __init__(self, outboxes: list[Any]) -> None:
        self.outboxes = outboxes
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> _FakeResult:
        self.statement = statement
        return _FakeResult(self.outboxes)


def _compiled_status_values(statement: Any) -> set[SystemOutboxStatus]:
    values: set[SystemOutboxStatus] = set()
    for value in statement.compile().params.values():
        if isinstance(value, list):
            values.update(item for item in value if isinstance(item, SystemOutboxStatus))
    return values


@pytest.mark.asyncio
async def test_cancel_active_by_session_treats_blocked_resource_as_active() -> None:
    blocked_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        last_error=None,
        finished_at=None,
    )
    db = _CapturingDb([blocked_outbox])

    count = await SystemOutboxRepository().cancel_active_by_session(
        db,
        session_id=7001,
        reason="MANUAL_CANCEL_REQUESTED",
    )

    assert db.statement is not None
    assert SystemOutboxStatus.BLOCKED_RESOURCE in _compiled_status_values(db.statement)
    assert count == 1
    assert blocked_outbox.status == SystemOutboxStatus.CANCELLED
    assert blocked_outbox.last_error == "MANUAL_CANCEL_REQUESTED"
    assert blocked_outbox.finished_at is not None
