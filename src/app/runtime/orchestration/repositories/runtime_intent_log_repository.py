"""RuntimeIntentLog 权威写入 Repository。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot


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
            prepared.append(
                self._build_prepared(
                    value,
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
            dispatch_status="PENDING",
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
    return parts[index] or None


def _completion_mode(intent: RuntimeIntent) -> str | None:
    if intent.kind is not RuntimeIntentKind.SYSTEM_CAPABILITY:
        return None
    from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX

    definition = SYSTEM_CAPABILITY_INDEX.get((str(intent.capability_key), str(intent.contract_version)))
    return definition.completion_mode.value if definition is not None else None


runtime_intent_log_repository = RuntimeIntentLogRepository()

__all__ = ["PreparedRuntimeIntentLog", "RuntimeIntentLogRepository", "runtime_intent_log_repository"]
