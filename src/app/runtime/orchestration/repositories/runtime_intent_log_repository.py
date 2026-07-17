"""RuntimeIntentLog 权威写入 Repository。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.utils.timezone import timezone

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


class RuntimeIntentLogRepository:
    """只持有 RuntimeIntentLog ledger，拒绝写插件 state、Timeline 或 Inbox 终态。"""

    def __init__(self, *, idempotency_guard: Any = None) -> None:
        self._idempotency_guard = idempotency_guard

    async def persist_attempt_intents(
        self,
        db: Any,
        *,
        locked: Any,
        snapshot: AttemptSnapshot,
        intents: Sequence[Any],
    ) -> None:
        inbox = locked.inbox
        execution_session_id = getattr(inbox, "execution_session_id", None)
        correlation_id = getattr(inbox, "correlation_id", None)
        if not isinstance(execution_session_id, int) or not isinstance(correlation_id, str) or not correlation_id:
            raise ValueError("plugin intent ledger requires execution_session_id and correlation_id")
        if snapshot.binding_id is None or snapshot.binding_version is None:
            raise ValueError("plugin intent ledger requires pinned binding identity")

        for ordinal, value in enumerate(intents):
            if not isinstance(value, RuntimeIntent):
                raise TypeError("plugin attempt intents must be RuntimeIntent")
            row = self._build_row(
                value,
                ordinal=ordinal,
                inbox_id=getattr(inbox, "id", None),
                execution_session_id=execution_session_id,
                correlation_id=correlation_id,
                snapshot=snapshot,
            )
            guard = self._idempotency_guard
            if guard is None:
                # 延迟解析避免 repositories package 初始化时反向触发 services 聚合导入。
                from src.app.runtime.orchestration.services.idempotency_guard import idempotency_guard

                guard = idempotency_guard
            claim_result = await guard.claim_or_match(
                db,
                provider_code=row.provider_code,
                operation_kind="plugin_intent",
                idempotency_key=row.idempotency_key,
                request_hash=row.request_hash,
                execution_correlation_id=correlation_id,
                now_ms=int(timezone.now_utc().timestamp() * 1000),
                business_owner_key=_bounded_identity(
                    f"{snapshot.definition_identity}:{snapshot.binding_identity}:{snapshot.index_digest}",
                    limit=160,
                ),
            )
            if getattr(claim_result, "value", claim_result) == "MATCH":
                continue
            db.add(row)

    def _build_row(
        self,
        intent: RuntimeIntent,
        *,
        ordinal: int,
        inbox_id: Any,
        execution_session_id: int,
        correlation_id: str,
        snapshot: AttemptSnapshot,
    ) -> RuntimeIntentLog:
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
        return RuntimeIntentLog(
            execution_session_id=execution_session_id,
            correlation_id=correlation_id,
            provider_code=_bounded_identity(intent.source_system or "workline-plugin", limit=60),
            target_domain=_target_domain(intent.kind),
            target_action=_bounded_identity(intent.action or intent.kind.value, limit=120),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            dispatch_status="PENDING",
        )


def _target_domain(kind: RuntimeIntentKind) -> str:
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


runtime_intent_log_repository = RuntimeIntentLogRepository()

__all__ = ["RuntimeIntentLogRepository", "runtime_intent_log_repository"]
