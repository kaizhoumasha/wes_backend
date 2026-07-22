"""RuntimeIntentLog 权威写入 Repository。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.system_capability_effect_claim import (
    SystemCapabilityClaimResult,
    SystemCapabilityIdempotencyConflict,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot
    from src.app.sys.models.outbox import SystemOutbox


_DEVICE_INTENTS = frozenset({RuntimeIntentKind.COMMAND, RuntimeIntentKind.DEVICE_EVENT})
_HANDLING_INTENTS = frozenset(
    {
        RuntimeIntentKind.RACK_OPERATION_REQUEST,
        RuntimeIntentKind.BIN_OPERATION_REQUEST,
        RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST,
    }
)
_WMS_INTENTS = frozenset({RuntimeIntentKind.EXTERNAL_REQUEST})


@dataclass(frozen=True, slots=True)
class PreparedRuntimeIntentLog:
    """Service 层执行幂等 claim 所需的稳定数据与待写 ledger model。"""

    claim: dict[str, Any]
    model: RuntimeIntentLog


class RuntimeIntentLogRepository:
    """只持有 RuntimeIntentLog ledger，拒绝写插件 state、Timeline 或 Inbox 终态。"""

    def prepare_attempt_intents(
        self,
        *,
        locked: Any,
        snapshot: AttemptSnapshot,
        intents: Sequence[Any],
    ) -> tuple[PreparedRuntimeIntentLog, ...]:
        inbox = locked.inbox
        execution_session_id = getattr(inbox, "execution_session_id", None)
        correlation_id = getattr(inbox, "correlation_id", None)
        if not isinstance(execution_session_id, int) or not isinstance(correlation_id, str) or not correlation_id:
            raise ValueError("plugin intent ledger requires execution_session_id and correlation_id")
        if snapshot.binding_id is None or snapshot.binding_version is None:
            raise ValueError("plugin intent ledger requires pinned binding identity")

        prepared: list[PreparedRuntimeIntentLog] = []
        for ordinal, value in enumerate(intents):
            if not isinstance(value, RuntimeIntent):
                raise TypeError("plugin attempt intents must be RuntimeIntent")
            validated_intent = RuntimeIntent.model_validate(value.model_dump(mode="python"))
            prepared.append(
                self._build_prepared(
                    validated_intent,
                    ordinal=ordinal,
                    inbox_id=getattr(inbox, "id", None),
                    execution_session_id=execution_session_id,
                    correlation_id=correlation_id,
                    snapshot=snapshot,
                )
            )
        return tuple(prepared)

    def add_prepared(self, db: Any, prepared: PreparedRuntimeIntentLog) -> None:
        db.add(prepared.model)

    async def add_proposed_pair(
        self,
        db: Any,
        *,
        intent_log: RuntimeIntentLog,
        outbox: SystemOutbox,
    ) -> None:
        """在调用方事务中原子加入 1:1 RuntimeIntentLog/SystemOutbox。"""

        if intent_log.dispatch_key != outbox.dispatch_key:
            raise ValueError("RuntimeIntentLog/SystemOutbox dispatch_key 必须一致")
        db.add_all((intent_log, outbox))
        await db.flush()

    async def claim_or_match(self, db: Any, **values: Any) -> SystemCapabilityClaimResult:
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
            "plugin_key": values["plugin_key"],
            "plugin_contract_version": values["plugin_contract_version"],
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
            "dispatch_key": values.get("dispatch_key") or values["idempotency_key"],
            "effect_status": RuntimeIntentStatus.PROPOSED,
            "outcome_json": {},
            "outcome_history_json": [],
            "effect_updated_at_ms": updated_at_ms,
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
        if row.request_hash != values["request_hash"]:
            raise SystemCapabilityIdempotencyConflict(
                **identity,
                existing_request_hash=row.request_hash,
                incoming_request_hash=values["request_hash"],
                correlation_id=row.correlation_id,
            )
        if row.effect_status in {
            RuntimeIntentStatus.COMPLETED,
            RuntimeIntentStatus.REJECTED,
            RuntimeIntentStatus.TECHNICAL_FAILED,
        }:
            return SystemCapabilityClaimResult.MATCH
        row.effect_status = RuntimeIntentStatus.PROPOSED
        row.outcome_kind = None
        row.outcome_code = None
        row.outcome_json = {}
        row.effect_updated_at_ms = insert_values["effect_updated_at_ms"]
        await db.flush()
        return SystemCapabilityClaimResult.NEW

    async def record_outcome(self, db: Any, *, claim: dict[str, Any], evidence: Any) -> None:
        row = await self._get_effect_for_update(
            db,
            provider_code=claim["provider_code"],
            operation_kind=claim["operation_kind"],
            idempotency_key=claim["idempotency_key"],
        )
        if row is None:
            raise RuntimeError("runtime intent provisional effect claim is missing")
        serialized = evidence.model_dump(mode="json")
        row.outcome_kind = evidence.outcome_kind
        row.outcome_code = evidence.outcome_code
        row.outcome_json = serialized
        row.outcome_history_json = [*list(row.outcome_history_json or []), serialized]
        row.effect_status = {
            "success": RuntimeIntentStatus.COMPLETED,
            "business_reject": RuntimeIntentStatus.REJECTED,
            "retryable_failure": RuntimeIntentStatus.TECHNICAL_FAILED,
            "contract_violation": RuntimeIntentStatus.TECHNICAL_FAILED,
        }[evidence.outcome_kind]
        row.effect_updated_at_ms = evidence.occurred_at_ms
        await db.flush()

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

    def _build_prepared(
        self,
        intent: RuntimeIntent,
        *,
        ordinal: int,
        inbox_id: Any,
        execution_session_id: int,
        correlation_id: str,
        snapshot: AttemptSnapshot,
    ) -> PreparedRuntimeIntentLog:
        operation_key = intent.idempotency_key or f"inbox:{inbox_id}:intent:{ordinal}"
        raw_idempotency_key = f"plugin-attempt:{snapshot.binding_identity}:{operation_key}"
        idempotency_key = _bounded_identity(raw_idempotency_key, limit=160)
        request_material = {
            "definition_identity": snapshot.definition_identity,
            "binding_identity": snapshot.binding_identity,
            "index_digest": snapshot.index_digest,
            "intent": intent.model_dump(mode="json"),
        }
        request_hash = sha256(
            json.dumps(request_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        model = RuntimeIntentLog(
            execution_session_id=execution_session_id,
            correlation_id=correlation_id,
            provider_code=_bounded_identity(intent.source_system or "workline-plugin", limit=60),
            target_domain=_target_domain(intent.kind, capability_key=intent.capability_key),
            target_action=_bounded_identity(intent.action or intent.kind.value, limit=120),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            dispatch_key=_bounded_identity(intent.dispatch_key or idempotency_key, limit=240),
            plugin_key=_definition_part(snapshot.definition_identity, 0),
            plugin_contract_version=_definition_part(snapshot.definition_identity, 1),
            capability_key=intent.capability_key,
            capability_contract_version=intent.contract_version,
            operation_identity=intent.operation_key,
            creator_authority=intent.creator_authority,
            authorization_policy=intent.authorization_policy,
            binding_snapshot_json=dict(intent.binding_snapshot),
            provider_snapshot_json=dict(intent.provider_snapshot),
            precondition_json=dict(intent.precondition_json),
            fact_version=str(intent.fact_version) if intent.fact_version is not None else None,
            payload_hash=intent.payload_hash,
            completion_mode=_completion_mode(intent),
        )
        return PreparedRuntimeIntentLog(
            claim={
                "provider_code": model.provider_code,
                "operation_kind": "plugin_intent",
                "idempotency_key": model.idempotency_key,
                "request_hash": model.request_hash,
                "execution_correlation_id": correlation_id,
                "business_owner_key": _bounded_identity(
                    f"{snapshot.definition_identity}:{snapshot.binding_identity}:{snapshot.index_digest}",
                    limit=160,
                ),
            },
            model=model,
        )


def _target_domain(kind: RuntimeIntentKind, *, capability_key: str | None = None) -> str:
    if kind is RuntimeIntentKind.SYSTEM_CAPABILITY and isinstance(capability_key, str):
        return capability_key.split(".", maxsplit=1)[0]
    if kind in _DEVICE_INTENTS:
        return "device"
    if kind in _HANDLING_INTENTS:
        return "handling"
    if kind in _WMS_INTENTS:
        return "wms_integration"
    return "runtime"


def _bounded_identity(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[: limit - len(digest) - 1]}:{digest}"


def _definition_part(identity: str | None, index: int) -> str | None:
    if not isinstance(identity, str) or "@" not in identity:
        return None
    parts = identity.split("@", maxsplit=1)
    value = parts[index]
    # Definition identity 的合同版本后附 schema digest；ledger 的独立
    # plugin_contract_version 列只保存合同版本，digest 已由 request_hash 固定。
    if index == 1:
        value = value.split(":", maxsplit=1)[0]
    return value or None


def _completion_mode(intent: RuntimeIntent) -> str | None:
    if intent.kind is not RuntimeIntentKind.SYSTEM_CAPABILITY:
        return None
    from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX

    definition = SYSTEM_CAPABILITY_INDEX.get((str(intent.capability_key), str(intent.contract_version)))
    return definition.completion_mode.value if definition is not None else None


runtime_intent_log_repository = RuntimeIntentLogRepository()

__all__ = ["PreparedRuntimeIntentLog", "RuntimeIntentLogRepository", "runtime_intent_log_repository"]
