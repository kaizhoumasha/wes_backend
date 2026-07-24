from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.services.system_outbox_cancellation_service import (
    SystemOutboxCancellationService,
)
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.repositories.outbox_repository import CancelledSystemOutbox


class _Repository:
    async def cancel_active_by_session(self, _db: Any, *, session_id: int, reason: str):
        assert session_id == 31
        assert reason == "SESSION_CANCELLED"
        return (
            CancelledSystemOutbox(
                dispatch_key="dispatch-cancelled",
                previous_status=SystemOutboxStatus.DISPATCHING,
            ),
        )


class _Reducer:
    def __init__(self) -> None:
        self.calls: list[tuple[object, bool]] = []

    async def reduce(self, _db: Any, event: object, *, require_intent: bool = True) -> None:
        self.calls.append((event, require_intent))


@pytest.mark.asyncio
async def test_session_cancellation_records_reconciliation_event_in_same_transaction() -> None:
    reducer = _Reducer()
    service = SystemOutboxCancellationService(repository=_Repository(), reducer=reducer)

    count = await service.cancel_active_by_session(
        SimpleNamespace(),
        session_id=31,
        reason="SESSION_CANCELLED",
    )

    assert count == 1
    event, require_intent = reducer.calls[0]
    assert event.event_type is EffectReducerEventType.DISPATCH_CANCELLED
    assert event.dispatch_key == "dispatch-cancelled"
    assert event.evidence_json == {
        "previous_outbox_status": SystemOutboxStatus.DISPATCHING.value,
        "reason": "SESSION_CANCELLED",
    }
    assert require_intent is False
