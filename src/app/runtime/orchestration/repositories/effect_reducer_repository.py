"""EFFECT reducer 的行锁与 ReconciliationCase 持久化边界。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog


class EffectReducerRepository:
    """只负责 reducer 所需的 runtime 域数据库访问。"""

    async def get_intent_for_update(self, db: Any, dispatch_key: str) -> RuntimeIntentLog | None:
        columns = cast("Any", RuntimeIntentLog).__table__.c
        result = await db.execute(
            select(RuntimeIntentLog).where(columns.dispatch_key == dispatch_key).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_open_case_for_update(self, db: Any, dispatch_key: str) -> ReconciliationCase | None:
        columns = cast("Any", ReconciliationCase).__table__.c
        result = await db.execute(
            select(ReconciliationCase)
            .where(
                columns.dispatch_key == dispatch_key,
                columns.status == ReconciliationCaseStatus.OPEN,
            )
            .order_by(columns.id.desc())
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    def add_case(db: Any, case: ReconciliationCase) -> None:
        db.add(case)


effect_reducer_repository = EffectReducerRepository()


__all__ = ["EffectReducerRepository", "effect_reducer_repository"]
