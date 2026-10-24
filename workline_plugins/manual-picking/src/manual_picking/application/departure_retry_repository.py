"""工作线锁内维护本轮清退退避；WMS 重新决定与游标推进同事务。"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from .departure_retry_model import ManualPickingDepartureRetry
from .rack_retry import rack_retry_delay_ms


class DepartureRetryRepository:
    async def observe_rejection(
        self,
        db: Any,
        *,
        workline_id: int,
        scope_identity: str,
        rack_id: str,
        step: str,
        transport_task_id: str,
        causal_token: int,
        prior_attempt: int,
        now: Any,
    ) -> ManualPickingDepartureRetry | None:
        row = await db.scalar(
            select(ManualPickingDepartureRetry)
            .where(
                ManualPickingDepartureRetry.workline_id == workline_id,
                ManualPickingDepartureRetry.scope_identity == scope_identity,
                ManualPickingDepartureRetry.rack_id == rack_id,
                ManualPickingDepartureRetry.step == step,
            )
            .with_for_update()
        )
        if row is not None:
            if causal_token < row.last_transport_causal_token:
                return None
            if causal_token == row.last_transport_causal_token:
                if row.last_transport_task_id != transport_task_id:
                    raise ValueError("departure retry causal identity differs")
                return row
            count = min(max(row.retry_count + 1, prior_attempt + 1), 7)
        else:
            count = min(prior_attempt + 1, 7)
            row = ManualPickingDepartureRetry(
                workline_id=workline_id,
                scope_identity=scope_identity,
                rack_id=rack_id,
                step=step,
                retry_count=count,
                last_transport_task_id=transport_task_id,
                last_transport_causal_token=causal_token,
                next_attempt_at=now,
                created_at=now,
            )
            db.add(row)
        row.retry_count = count
        row.last_transport_task_id = transport_task_id
        row.last_transport_causal_token = causal_token
        row.next_attempt_at = now + timedelta(milliseconds=rack_retry_delay_ms(count - 1))
        row.decision_operation_id = None
        row.updated_at = now
        await db.flush()
        return row

    async def record_decision(self, db: Any, row: ManualPickingDepartureRetry, operation_id: str, now: Any) -> None:
        if row.decision_operation_id not in (None, operation_id):
            raise ValueError("departure retry already owns another decision")
        row.decision_operation_id = operation_id
        row.updated_at = now
        await db.flush()
