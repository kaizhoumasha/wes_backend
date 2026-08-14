"""RuntimeIntentLog 权威写入 Repository。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.system_capability_effect_claim import (
    SystemCapabilityAdmissionClosed,
    SystemCapabilityClaimResult,
    SystemCapabilityIdempotencyConflict,
)


class RuntimeIntentLogRepository:
    """只持有 RuntimeIntentLog ledger，拒绝写插件 state、Timeline 或 Inbox 终态。"""

    async def add_proposed_pair(
        self,
        db: Any,
        *,
        intent_log: RuntimeIntentLog,
        outbox: Any,
    ) -> None:
        """在调用方事务中原子加入 1:1 RuntimeIntentLog/SystemOutbox。"""

        if not intent_log.dispatch_key or not outbox.dispatch_key:
            raise ValueError("RuntimeIntentLog/SystemOutbox 必须显式提供 dispatch_key")
        if intent_log.dispatch_key != outbox.dispatch_key:
            raise ValueError("RuntimeIntentLog/SystemOutbox dispatch_key 必须一致")
        if intent_log.effect_status != RuntimeIntentStatus.PROPOSED:
            raise ValueError("RuntimeIntentLog 必须以 PROPOSED 状态加入 EFFECT 双账本")
        from src.app.sys.models.outbox import SystemOutboxStatus

        if outbox.status != SystemOutboxStatus.NEW:
            raise ValueError("SystemOutbox 必须以 NEW 状态加入 EFFECT 双账本")
        db.add_all((intent_log, outbox))
        await db.flush()

    async def claim_or_match(
        self,
        db: Any,
        *,
        allow_insert: bool = True,
        **values: Any,
    ) -> SystemCapabilityClaimResult:
        """在唯一 RuntimeIntentLog ledger 上执行 provisional claim。"""

        table = cast("Any", RuntimeIntentLog).__table__
        identity = {
            "provider_code": values["provider_code"],
            "operation_kind": values["operation_kind"],
            "idempotency_key": values["idempotency_key"],
        }
        dialect_name = db.get_bind().dialect.name
        insert_fn = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        if dialect_name not in {"sqlite", "postgresql"}:
            raise NotImplementedError(f"runtime intent effect claim 暂不支持数据库方言: {dialect_name}")
        dispatch_key = values.get("dispatch_key")
        if not isinstance(dispatch_key, str) or not dispatch_key:
            raise ValueError("runtime intent effect claim 必须显式提供 dispatch_key")
        if not allow_insert:
            row = await self._get_effect_for_update(db, **identity)
            if row is None:
                raise SystemCapabilityAdmissionClosed
            return self._match_existing_claim(
                row,
                values=values,
                identity=identity,
                dispatch_key=dispatch_key,
            )
        updated_at_ms = values["updated_at_ms"]
        insert_values = {
            "execution_session_id": values["execution_session_id"],
            "execution_work_item_id": values["execution_work_item_id"],
            "correlation_id": values["correlation_id"],
            "provider_code": values["provider_code"],
            "operation_kind": values["operation_kind"],
            "target_domain": str(values["capability_key"]).split(".", maxsplit=1)[0],
            "target_action": values["operation_identity"],
            "idempotency_key": values["idempotency_key"],
            "request_hash": values["request_hash"],
            "capability_key": values["capability_key"],
            "capability_contract_version": values["capability_contract_version"],
            "operation_identity": values["operation_identity"],
            "creator_authority": values["creator_authority"],
            "authorization_policy": values["authorization_policy"],
            "binding_snapshot_json": values["binding_snapshot_json"],
            "provider_snapshot_json": values["provider_snapshot_json"],
            "precondition_json": values["precondition_json"],
            "fact_version": values["fact_version"],
            "payload_hash": values["payload_hash"],
            "completion_mode": values["completion_mode"],
            "dispatch_key": dispatch_key,
            "effect_status": RuntimeIntentStatus.PROPOSED,
            "outcome_json": {},
            "outcome_history_json": [],
            "effect_updated_at_ms": updated_at_ms,
            # Core INSERT 不会应用 SQLModel default_factory；
            # 非 WMS intent 以空快照表达“无 status binding”。
            "status_binding_snapshot_json": {},
        }
        inserted_id = (
            await db.execute(
                insert_fn(table)
                .values(**insert_values)
                .on_conflict_do_nothing(
                    index_elements=[table.c.provider_code, table.c.operation_kind, table.c.idempotency_key]
                )
                .returning(table.c.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return SystemCapabilityClaimResult.NEW
        row = await self._get_effect_for_update(db, **identity)
        if row is None:
            raise RuntimeError("runtime intent effect claim conflict row disappeared")
        return self._match_existing_claim(
            row,
            values=values,
            identity=identity,
            dispatch_key=dispatch_key,
        )

    @staticmethod
    def _match_existing_claim(
        row: RuntimeIntentLog,
        *,
        values: dict[str, Any],
        identity: dict[str, Any],
        dispatch_key: str,
    ) -> SystemCapabilityClaimResult:
        """统一校验 insert-conflict 与 existing-only 命中的不可变 claim。"""

        if row.request_hash != values["request_hash"]:
            raise SystemCapabilityIdempotencyConflict(
                **identity,
                existing_request_hash=row.request_hash,
                incoming_request_hash=values["request_hash"],
                correlation_id=row.correlation_id,
            )
        is_runtime_domain_claim = (
            row.creator_authority == "RUNTIME_DOMAIN_SERVICE" or values["creator_authority"] == "RUNTIME_DOMAIN_SERVICE"
        )
        if is_runtime_domain_claim and (
            row.correlation_id != values["correlation_id"]
            or dict(row.binding_snapshot_json) != dict(values["binding_snapshot_json"])
        ):
            # domain 幂等键故意不含 owner/workline/correlation；命中时必须对权威快照
            # 做完整 identity reconciliation，禁止同 payload 借旧 ledger 越权重放。
            raise SystemCapabilityIdempotencyConflict(
                **identity,
                existing_request_hash=row.request_hash,
                incoming_request_hash=values["request_hash"],
                correlation_id=row.correlation_id,
            )
        if row.dispatch_key != dispatch_key:
            raise ValueError("RuntimeIntentLog.dispatch_key 持久化后不可变")
        if row.effect_status in {
            RuntimeIntentStatus.COMPLETED,
            RuntimeIntentStatus.REJECTED,
            RuntimeIntentStatus.TECHNICAL_FAILED,
        }:
            return SystemCapabilityClaimResult.MATCH
        # 唯一约束冲突表示同一 immutable request 已有权威 ledger；包括
        # OUTBOX_ASYNC 的 PROPOSED 双账本，调用方必须重放既有状态，不能再次执行 handler。
        return SystemCapabilityClaimResult.MATCH

    async def get_success_evidence(self, db: Any, *, claim: dict[str, Any]) -> dict[str, object] | None:
        row = await self._get_effect_for_update(
            db,
            provider_code=claim["provider_code"],
            operation_kind=claim["operation_kind"],
            idempotency_key=claim["idempotency_key"],
        )
        if row is None or row.effect_status != RuntimeIntentStatus.COMPLETED or row.outcome_kind != "success":
            return None
        return dict(row.outcome_json)

    async def get_claimed_intent(self, db: Any, *, claim: dict[str, Any]) -> RuntimeIntentLog | None:
        """锁定并返回刚 claim 的权威 RuntimeIntentLog，供同事务双账本写入。"""

        return await self._get_effect_for_update(
            db,
            provider_code=claim["provider_code"],
            operation_kind=claim["operation_kind"],
            idempotency_key=claim["idempotency_key"],
        )

    async def has_claimed_outbox(self, db: Any, *, claim: dict[str, Any]) -> bool:
        """确认 provisional intent 已拥有同 dispatch key 的 durable outbox。"""

        from src.app.sys.models.outbox import SystemOutbox

        columns = cast("Any", SystemOutbox).__table__.c
        from src.app.sys.models.outbox import SystemOutboxStatus

        result = await db.execute(
            select(columns.id)
            .where(
                columns.dispatch_key == claim["dispatch_key"],
                columns.status.in_(
                    {
                        SystemOutboxStatus.NEW,
                        SystemOutboxStatus.DISPATCHING,
                        SystemOutboxStatus.RETRY_WAIT,
                        SystemOutboxStatus.SENT,
                    }
                ),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_conflicted_intent_for_update(
        self,
        db: Any,
        *,
        provider_code: str,
        operation_kind: str,
        idempotency_key: str,
    ) -> RuntimeIntentLog | None:
        """只按冲突携带的稳定 identity 锁定既有权威 intent。"""

        return await self._get_effect_for_update(
            db,
            provider_code=provider_code,
            operation_kind=operation_kind,
            idempotency_key=idempotency_key,
        )

    async def list_redecision_evidence(
        self, db: Any, *, execution_session_id: int, execution_work_item_id: int
    ) -> tuple[dict[str, object], ...]:
        columns = cast("Any", RuntimeIntentLog).__table__.c
        result = await db.execute(
            select(RuntimeIntentLog)
            .where(
                columns.execution_session_id == execution_session_id,
                columns.execution_work_item_id == execution_work_item_id,
                columns.operation_kind == "system_capability_effect",
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
    async def _get_effect_for_update(db: Any, **identity: Any) -> RuntimeIntentLog | None:
        columns = cast("Any", RuntimeIntentLog).__table__.c
        result = await db.execute(
            select(RuntimeIntentLog)
            .where(
                columns.provider_code == identity["provider_code"],
                columns.operation_kind == identity["operation_kind"],
                columns.idempotency_key == identity["idempotency_key"],
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()


runtime_intent_log_repository = RuntimeIntentLogRepository()

__all__ = ["RuntimeIntentLogRepository", "runtime_intent_log_repository"]
