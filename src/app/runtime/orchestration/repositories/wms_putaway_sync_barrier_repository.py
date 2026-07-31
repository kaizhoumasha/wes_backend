"""E03/E07 投格同步屏障的窄持久化边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldStatus
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.utils.timezone import timezone


@dataclass(frozen=True, slots=True)
class WmsPutawaySyncObligation:
    """单项 WMS 同步义务及其对账裁决快照。"""

    operation_identity: str
    fact_version: str
    intent_status: str
    has_open_case: bool
    resolved_decisions: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class WmsPutawaySyncBarrierSnapshot:
    """同一投格事实的 E03/E07 行锁快照。"""

    obligations: tuple[WmsPutawaySyncObligation, ...]


@dataclass(frozen=True, slots=True)
class WmsPutawaySyncDispatchIdentity:
    """由权威 Intent 读取的屏障分组身份。"""

    operation_identity: str
    execution_work_item_id: int | None
    correlation_id: str | None
    fact_version: str | None


class WmsPutawaySyncBarrierRepository:
    """只锁定和更新同步屏障需要的 Intent、Case 与 Hold。"""

    async def get_dispatch_identity(self, db: Any, *, dispatch_key: str) -> WmsPutawaySyncDispatchIdentity | None:
        columns = cast("Any", RuntimeIntentLog).__table__.c
        result = await db.execute(
            select(
                columns.operation_identity,
                columns.execution_work_item_id,
                columns.correlation_id,
                columns.fact_version,
            )
            .where(columns.dispatch_key == dispatch_key)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return WmsPutawaySyncDispatchIdentity(
            operation_identity=str(row.operation_identity or ""),
            execution_work_item_id=row.execution_work_item_id,
            correlation_id=row.correlation_id,
            fact_version=row.fact_version,
        )

    async def lock_group_mutex(
        self,
        db: Any,
        *,
        execution_work_item_id: int,
        correlation_id: str,
    ) -> bool:
        """先锁共享 WorkItem，统一 E03/E07 并发事务的锁序。"""

        columns = cast("Any", ExecutionWorkItem).__table__.c
        result = await db.execute(
            select(columns.id)
            .where(
                columns.id == execution_work_item_id,
                columns.correlation_id == correlation_id,
            )
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none() is not None

    async def load_group_for_update(self, db: Any, group: Any) -> WmsPutawaySyncBarrierSnapshot:
        intent_columns = cast("Any", RuntimeIntentLog).__table__.c
        intent_result = await db.execute(
            select(RuntimeIntentLog)
            .where(
                intent_columns.execution_work_item_id == group.execution_work_item_id,
                intent_columns.correlation_id == group.correlation_id,
                intent_columns.fact_version == group.fact_version,
                intent_columns.operation_identity.in_(group.required_operation_identities),
            )
            .order_by(intent_columns.operation_identity.asc(), intent_columns.id.asc())
            .with_for_update()
        )
        intents = tuple(intent_result.scalars().all())
        intent_ids = tuple(int(intent.id) for intent in intents if isinstance(intent.id, int))
        cases: tuple[ReconciliationCase, ...] = ()
        if intent_ids:
            case_columns = cast("Any", ReconciliationCase).__table__.c
            case_result = await db.execute(
                select(ReconciliationCase)
                .where(case_columns.runtime_intent_log_id.in_(intent_ids))
                .order_by(case_columns.runtime_intent_log_id.asc(), case_columns.id.asc())
                .with_for_update()
            )
            cases = tuple(case_result.scalars().all())

        cases_by_intent: dict[int, list[ReconciliationCase]] = {}
        for case in cases:
            cases_by_intent.setdefault(case.runtime_intent_log_id, []).append(case)
        obligations = tuple(
            WmsPutawaySyncObligation(
                operation_identity=str(intent.operation_identity or ""),
                fact_version=str(intent.fact_version or ""),
                intent_status=str(getattr(intent.effect_status, "value", intent.effect_status)),
                has_open_case=any(
                    case.status == ReconciliationCaseStatus.OPEN for case in cases_by_intent.get(int(intent.id), ())
                ),
                resolved_decisions=tuple(
                    dict(case.decision_json or {})
                    for case in cases_by_intent.get(int(intent.id), ())
                    if case.status == ReconciliationCaseStatus.RESOLVED
                ),
            )
            for intent in intents
            if isinstance(intent.id, int)
        )
        return WmsPutawaySyncBarrierSnapshot(obligations=obligations)

    async def get_hold_for_update(self, db: Any, *, source_idempotency_key: str) -> RuntimeHold | None:
        columns = cast("Any", RuntimeHold).__table__.c
        result = await db.execute(
            select(RuntimeHold)
            .where(columns.source_idempotency_key == source_idempotency_key)
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_hold_resolved(
        db: Any,
        hold: RuntimeHold,
        *,
        release_evidence: dict[str, object],
    ) -> bool:
        """仅执行对象同步 Hold 的 OPEN→RESOLVED，不触碰 Session/WorkLine。"""

        if hold.status != RuntimeHoldStatus.OPEN:
            return False
        hold.status = RuntimeHoldStatus.RESOLVED
        hold.release_evidence_json = release_evidence
        hold.resolved_at = timezone.now_for_db()
        hold.increment_version()
        await db.flush()
        return True


wms_putaway_sync_barrier_repository = WmsPutawaySyncBarrierRepository()

__all__ = [
    "WmsPutawaySyncBarrierRepository",
    "WmsPutawaySyncBarrierSnapshot",
    "WmsPutawaySyncDispatchIdentity",
    "WmsPutawaySyncObligation",
    "wms_putaway_sync_barrier_repository",
]
