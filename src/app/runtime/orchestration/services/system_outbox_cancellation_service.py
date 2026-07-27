"""SystemOutbox 取消与 EFFECT 语义账本的同事务协调。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    generated_effect_source_event_id,
)
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer, effect_reducer
from src.app.sys.repositories.outbox_repository import (
    CancelledSystemOutbox,
    SystemOutboxRepository,
)
from src.utils.timezone import timezone


class SystemOutboxCancellationService:
    """取消 outbox，并让存在配对 intent 的 EFFECT 进入显式对账。"""

    def __init__(
        self,
        *,
        repository: SystemOutboxRepository | None = None,
        reducer: EffectReducer = effect_reducer,
    ) -> None:
        self._repository = repository or SystemOutboxRepository()
        self._reducer = reducer

    async def cancel_active_by_workline(self, db: Any, workline_id: int, *, incident_id: int) -> int:
        reason = f"CANCELLED_BY_ESTOP:incident_id={incident_id}"
        cancelled = await self._repository.cancel_active_by_workline(
            db,
            workline_id,
            incident_id=incident_id,
        )
        await self._record_cancellations(db, cancelled=cancelled, reason=reason)
        return len(cancelled)

    async def cancel_active_by_session(self, db: Any, *, session_id: int, reason: str) -> int:
        cancelled = await self._repository.cancel_active_by_session(
            db,
            session_id=session_id,
            reason=reason,
        )
        await self._record_cancellations(db, cancelled=cancelled, reason=reason)
        return len(cancelled)

    async def _record_cancellations(
        self,
        db: Any,
        *,
        cancelled: tuple[CancelledSystemOutbox, ...],
        reason: str,
    ) -> None:
        occurred_at_ms = int(timezone.now_utc().timestamp() * 1000)
        for item in cancelled:
            _ = await self._reducer.reduce(
                db,
                EffectReducerEvent(
                    event_type=EffectReducerEventType.DISPATCH_CANCELLED,
                    dispatch_key=item.dispatch_key,
                    occurred_at_ms=occurred_at_ms,
                    source_event_id=generated_effect_source_event_id(
                        "outbox-cancelled",
                        item.dispatch_key,
                        item.previous_status.value,
                        reason,
                    ),
                    reason_code="OUTBOX_CANCELLED",
                    evidence_json={
                        "previous_outbox_status": item.previous_status.value,
                        "reason": reason,
                    },
                ),
                require_intent=False,
            )


system_outbox_cancellation_service = SystemOutboxCancellationService()

__all__ = [
    "SystemOutboxCancellationService",
    "system_outbox_cancellation_service",
]
