"""SYSTEM_CAPABILITY EFFECT provisional claim 与 outcome evidence Repository。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.system_capability_effect_claim import (
    SystemCapabilityClaimResult,
    SystemCapabilityIdempotencyConflict,
)
from src.app.runtime.orchestration.system_capability_effect_record import SystemCapabilityEffectRecord


class SystemCapabilityEffectRepository:
    """持久化 EFFECT 生命周期；仅 SUCCEEDED 可返回幂等 MATCH。"""

    async def claim_or_match(self, db: Any, **values: Any) -> SystemCapabilityClaimResult:
        table = cast("Any", SystemCapabilityEffectRecord).__table__
        identity = {
            "provider_code": values["provider_code"],
            "operation_kind": values["operation_kind"],
            "idempotency_key": values["idempotency_key"],
        }
        dialect_name = db.get_bind().dialect.name
        insert_fn = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        if dialect_name not in {"sqlite", "postgresql"}:
            raise NotImplementedError(f"system capability claim 暂不支持数据库方言: {dialect_name}")
        inserted_id = (
            await db.execute(
                insert_fn(table)
                .values(**values, status="PROVISIONAL", attempt_count=1, outcome_json={}, outcome_history_json=[])
                .on_conflict_do_nothing(
                    index_elements=[table.c.provider_code, table.c.operation_kind, table.c.idempotency_key]
                )
                .returning(table.c.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return SystemCapabilityClaimResult.NEW
        row = await self._get_for_update(db, **identity)
        if row is None:
            raise RuntimeError("system capability claim conflict row disappeared")
        if row.request_hash != values["request_hash"]:
            raise SystemCapabilityIdempotencyConflict(
                **identity,
                existing_request_hash=row.request_hash,
                incoming_request_hash=values["request_hash"],
                correlation_id=row.correlation_id,
            )
        if row.status == "SUCCEEDED":
            return SystemCapabilityClaimResult.MATCH
        row.status = "PROVISIONAL"
        row.attempt_count += 1
        row.outcome_kind = None
        row.outcome_code = None
        row.outcome_json = {}
        row.updated_at_ms = values["updated_at_ms"]
        await db.flush()
        return SystemCapabilityClaimResult.NEW

    async def record_outcome(self, db: Any, *, claim: dict[str, Any], evidence: Any) -> None:
        row = await self._get_for_update(
            db,
            provider_code=claim["provider_code"],
            operation_kind=claim["operation_kind"],
            idempotency_key=claim["idempotency_key"],
        )
        if row is None:
            raise RuntimeError("system capability provisional claim is missing")
        row.outcome_kind = evidence.outcome_kind
        row.outcome_code = evidence.outcome_code
        row.outcome_json = evidence.model_dump(mode="json")
        row.outcome_history_json = [*list(row.outcome_history_json or []), evidence.model_dump(mode="json")]
        row.status = {
            "success": "SUCCEEDED",
            "business_reject": "BUSINESS_REJECT",
            "retryable_failure": "RETRYABLE_FAILURE",
            "contract_violation": "CONTRACT_VIOLATION",
        }[evidence.outcome_kind]
        row.updated_at_ms = evidence.occurred_at_ms
        await db.flush()

    async def list_redecision_evidence(
        self, db: Any, *, execution_session_id: int, execution_work_item_id: int
    ) -> tuple[dict[str, object], ...]:
        columns = cast("Any", SystemCapabilityEffectRecord).__table__.c
        result = await db.execute(
            select(SystemCapabilityEffectRecord)
            .where(
                columns.execution_session_id == execution_session_id,
                columns.execution_work_item_id == execution_work_item_id,
            )
            .order_by(columns.id.asc())
        )
        evidence: list[dict[str, object]] = []
        for row in result.scalars().all():
            evidence.extend(
                dict(item)
                for item in row.outcome_history_json
                if isinstance(item, dict) and item.get("outcome_kind") == "business_reject"
            )
        return tuple(evidence)

    @staticmethod
    async def _get_for_update(db: Any, **identity: Any) -> SystemCapabilityEffectRecord | None:
        columns = cast("Any", SystemCapabilityEffectRecord).__table__.c
        result = await db.execute(
            select(SystemCapabilityEffectRecord)
            .where(
                columns.provider_code == identity["provider_code"],
                columns.operation_kind == identity["operation_kind"],
                columns.idempotency_key == identity["idempotency_key"],
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()


system_capability_effect_repository = SystemCapabilityEffectRepository()

__all__ = ["SystemCapabilityEffectRepository", "system_capability_effect_repository"]
