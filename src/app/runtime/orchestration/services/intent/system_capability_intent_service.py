"""SYSTEM_CAPABILITY intent 的固定快照、幂等键与执行准备。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    generated_effect_source_event_id,
)
from src.app.runtime.orchestration.runtime_intent import (
    RuntimeIntent,
    RuntimeIntentKind,
    validate_system_capability_operation_key,
)
from src.app.runtime.orchestration.system_capability_effect_claim import SystemCapabilityClaimResult
from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition, SystemCapabilityMode
from src.core.logger import logger
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
    from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict
    from src.app.runtime.orchestration.system_capability_effect_claim import SystemCapabilityIdempotencyConflict


@dataclass(frozen=True, slots=True)
class PreparedSystemCapabilityIntent:
    definition: SystemCapabilityDefinition
    request: BaseModel
    admission: BaseModel
    idempotency_key: str
    payload_hash: str
    claim_result: SystemCapabilityClaimResult | Any
    claim: dict[str, Any]
    intent_log: RuntimeIntentLog | None
    has_durable_outbox: bool


class SystemCapabilityIntentService:
    """Runtime-owned EFFECT admission；不执行 handler，也不控制事务。"""

    def __init__(
        self,
        *,
        definitions: Mapping[tuple[str, str], SystemCapabilityDefinition] | None = None,
        plugin_definitions: Mapping[tuple[str, str], Any] | None = None,
        plugin_index_digest: str | None = None,
        effect_repository: Any | None = None,
        effect_reducer: Any | None = None,
        effect_reconciliation_bridge: Any | None = None,
    ) -> None:
        if definitions is None:
            from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX

            definitions = SYSTEM_CAPABILITY_INDEX
        self._definitions = dict(definitions)
        if plugin_definitions is None or plugin_index_digest is None:
            from src.app.runtime.workline_plugins.generated_index import (
                WORKLINE_PLUGIN_INDEX,
                WORKLINE_PLUGIN_INDEX_DIGEST,
            )

            plugin_definitions = WORKLINE_PLUGIN_INDEX if plugin_definitions is None else plugin_definitions
            plugin_index_digest = WORKLINE_PLUGIN_INDEX_DIGEST if plugin_index_digest is None else plugin_index_digest
        if effect_repository is None:
            from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
                runtime_intent_log_repository,
            )

            effect_repository = runtime_intent_log_repository
        self._effect_repository = effect_repository
        if effect_reducer is None:
            from src.app.runtime.orchestration.services.effect_reducer_service import effect_reducer

        self._effect_reducer = effect_reducer
        if effect_reconciliation_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_reconciliation_bridge

        self._effect_reconciliation_bridge = effect_reconciliation_bridge
        self._plugin_definitions = dict(plugin_definitions)
        self._plugin_index_digest = plugin_index_digest

    async def prepare_and_claim(self, ctx: Mapping[str, Any], intent: RuntimeIntent) -> PreparedSystemCapabilityIntent:
        # model_copy(update=...) 不执行 Pydantic validator；admission 必须从序列化值
        # 重建 typed contract，避免插件绕过 kind 独占字段和跨字段约束。
        intent = RuntimeIntent.model_validate(intent.model_dump(mode="python"))
        if intent.kind is not RuntimeIntentKind.SYSTEM_CAPABILITY:
            raise ValueError("intent must be SYSTEM_CAPABILITY")
        identity = (str(intent.capability_key), str(intent.contract_version))
        definition = self._definitions.get(identity)
        if definition is None:
            raise ValueError("system capability is not present in generated index")
        if definition.mode is not SystemCapabilityMode.EFFECT:
            raise ValueError("SYSTEM_CAPABILITY intent requires EFFECT definition")
        if not isinstance(intent.dispatch_key, str) or not intent.dispatch_key:
            raise ValueError("SYSTEM_CAPABILITY effect requires explicit dispatch_key")
        _ = validate_system_capability_operation_key(intent.operation_key)
        execution_identity = self._validate_execution_identity(ctx, intent, definition=definition)
        if intent.payload_hash != sha256_digest(intent.payload_json):
            raise ValueError("SYSTEM_CAPABILITY payload_hash mismatch")
        try:
            request = definition.input_model.model_validate(intent.payload_json)
            admission = definition.admission_model.model_validate(
                {
                    "precondition": intent.precondition_json,
                    "fact_version": intent.fact_version,
                }
            )
        except ValidationError as exc:
            raise ValueError("system capability typed payload validation failed") from exc

        final_key = self._final_idempotency_key(ctx, intent)
        correlation_id = self._correlation_id(ctx)
        claim = {
            "provider_code": "RUNTIME",
            "operation_kind": "system_capability_effect",
            "idempotency_key": final_key,
            "request_hash": str(intent.payload_hash),
            "execution_session_id": execution_identity["execution_session_id"],
            "execution_work_item_id": execution_identity["execution_work_item_id"],
            "correlation_id": correlation_id,
            "plugin_key": execution_identity["plugin_key"],
            "plugin_contract_version": execution_identity["plugin_contract_version"],
            "binding_id": execution_identity["binding_id"],
            "binding_version": execution_identity["binding_version"],
            "capability_key": str(intent.capability_key),
            "capability_contract_version": str(intent.contract_version),
            "operation_identity": str(intent.operation_key),
            "creator_authority": intent.creator_authority,
            "authorization_policy": intent.authorization_policy,
            "binding_snapshot_json": dict(intent.binding_snapshot),
            "provider_snapshot_json": dict(intent.provider_snapshot),
            "precondition_json": dict(intent.precondition_json),
            "fact_version": str(intent.fact_version),
            "payload_hash": str(intent.payload_hash),
            "completion_mode": definition.completion_mode.value,
            "dispatch_key": intent.dispatch_key,
            "updated_at_ms": int(timezone.now_utc().timestamp() * 1000),
        }
        claim_result = await self._effect_repository.claim_or_match(ctx["db"], **claim)
        intent_log = None
        has_durable_outbox = False
        is_match = getattr(claim_result, "value", claim_result) == SystemCapabilityClaimResult.MATCH.value
        if definition.completion_mode.value == "OUTBOX_ASYNC" or is_match:
            # OUTBOX_ASYNC 首次写入需要权威 intent；任意幂等命中也必须读取状态，
            # 才能区分可重新决策的 LOCAL PROPOSED 与已持久化终态。
            intent_log = await self._effect_repository.get_claimed_intent(ctx["db"], claim=claim)
            if intent_log is None:
                raise RuntimeError("system capability effect claim row is missing")
            if definition.completion_mode.value == "OUTBOX_ASYNC" and is_match:
                has_durable_outbox = await self._effect_repository.has_claimed_outbox(ctx["db"], claim=claim)
        return PreparedSystemCapabilityIntent(
            definition=definition,
            request=request,
            admission=admission,
            idempotency_key=final_key,
            payload_hash=str(intent.payload_hash),
            claim_result=claim_result,
            claim=claim,
            intent_log=intent_log,
            has_durable_outbox=has_durable_outbox,
        )

    async def record_outcome(
        self, ctx: Mapping[str, Any], *, prepared: PreparedSystemCapabilityIntent, evidence: Any
    ) -> None:
        event_type = {
            "success": EffectReducerEventType.CALLBACK_COMPLETED,
            "business_reject": EffectReducerEventType.LOCAL_REDECISION_REQUIRED,
            "retryable_failure": EffectReducerEventType.TRANSPORT_NOT_SENT,
            "contract_violation": EffectReducerEventType.TRANSPORT_NOT_SENT,
        }[evidence.outcome_kind]
        clearly_not_applied = event_type is EffectReducerEventType.TRANSPORT_NOT_SENT
        retry_exhausted = evidence.outcome_kind == "contract_violation"
        await self._effect_reducer.reduce(
            ctx["db"],
            EffectReducerEvent(
                event_type=event_type,
                dispatch_key=str(prepared.claim["dispatch_key"]),
                occurred_at_ms=evidence.occurred_at_ms,
                source_event_id=generated_effect_source_event_id(
                    "local-effect",
                    prepared.claim["dispatch_key"],
                    evidence.outcome_kind,
                    evidence.occurred_at_ms,
                ),
                attempt_no=1 if clearly_not_applied else None,
                retry_exhausted=retry_exhausted,
                reason_code=evidence.outcome_code,
                evidence_json=evidence.model_dump(mode="json"),
            ),
        )

    async def record_idempotency_conflict(
        self,
        ctx: Mapping[str, Any],
        *,
        conflict: IdempotencyConflict | SystemCapabilityIdempotencyConflict,
    ) -> bool:
        """锁定冲突 identity 的权威 intent，并在当前事务写入 reconciliation evidence。"""

        identity = {
            "provider_code": conflict.provider_code,
            "operation_kind": conflict.operation_kind,
            "idempotency_key": conflict.idempotency_key,
        }
        authoritative = await self._effect_repository.get_conflicted_intent_for_update(ctx["db"], **identity)
        dispatch_key = getattr(authoritative, "dispatch_key", None)
        intent_id = getattr(authoritative, "id", None)
        if (
            authoritative is None
            or not isinstance(dispatch_key, str)
            or not dispatch_key
            or not isinstance(intent_id, int)
        ):
            # 不能从 incoming payload/correlation 猜测关联；缺失权威行时保留 409 fail-closed，
            # 同时输出稳定诊断码，让未落 reconciliation evidence 的异常可观测。
            logger.error(
                "IDEMPOTENCY_CONFLICT_AUTHORITY_MISSING "
                f"provider={identity['provider_code']} operation={identity['operation_kind']} "
                f"idempotency_key={identity['idempotency_key']}"
            )
            return False

        evidence_json = {
            **identity,
            "existing_request_hash": conflict.existing_request_hash,
            "incoming_request_hash": conflict.incoming_request_hash,
            "authoritative_intent_id": intent_id,
        }
        source_material = "|".join(
            (
                identity["provider_code"],
                identity["operation_kind"],
                identity["idempotency_key"],
                str(conflict.incoming_request_hash or "missing"),
            )
        )
        await self._effect_reconciliation_bridge.record_idempotency_conflict(
            ctx["db"],
            dispatch_key=dispatch_key,
            occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
            source_event_id=f"idempotency-conflict:{sha256(source_material.encode('utf-8')).hexdigest()}",
            evidence_json=evidence_json,
        )
        return True

    async def get_success_evidence(
        self, ctx: Mapping[str, Any], *, prepared: PreparedSystemCapabilityIntent
    ) -> dict[str, object] | None:
        return await self._effect_repository.get_success_evidence(ctx["db"], claim=prepared.claim)

    def _validate_execution_identity(
        self, ctx: Mapping[str, Any], intent: RuntimeIntent, *, definition: SystemCapabilityDefinition
    ) -> dict[str, Any]:
        session = ctx.get("session")
        work_item = ctx.get("work_item")
        inbox = ctx.get("inbox")
        binding = ctx.get("plugin_binding")
        if session is None or work_item is None or inbox is None or binding is None:
            raise PermissionError("system capability requires locked session/work-item/binding identity")
        plugin_key = getattr(session, "plugin_key", None)
        plugin_contract_version = getattr(session, "contract_version", None)
        binding_id = getattr(session, "plugin_binding_id", None)
        binding_version = getattr(session, "plugin_binding_version", None)
        session_digest = getattr(session, "plugin_index_digest", None)
        if (
            not isinstance(plugin_key, str)
            or not isinstance(plugin_contract_version, str)
            or not isinstance(binding_id, int)
            or not isinstance(binding_version, int)
            or not isinstance(session_digest, str)
        ):
            raise PermissionError("system capability locked plugin pin is incomplete")
        expected_pin = (plugin_key, binding_id, binding_version, session_digest)
        work_item_pin = (
            getattr(work_item, "plugin_key", None),
            getattr(work_item, "plugin_binding_id", None),
            getattr(work_item, "plugin_binding_version", None),
            getattr(work_item, "plugin_index_digest", None),
        )
        if expected_pin != work_item_pin or session_digest != self._plugin_index_digest:
            raise PermissionError("system capability locked plugin pin mismatch")
        binding_pin = (
            getattr(binding, "plugin_key", None),
            getattr(binding, "contract_version", None),
            getattr(binding, "id", None),
            getattr(binding, "binding_version", None),
            getattr(binding, "generated_index_digest", None),
        )
        if binding_pin != (plugin_key, plugin_contract_version, binding_id, binding_version, session_digest):
            raise PermissionError("system capability immutable binding row mismatch")
        if getattr(binding, "is_enabled", True) is not True or getattr(binding, "is_revoked", False) is True:
            raise PermissionError("system capability binding is disabled or revoked")
        plugin_definition = self._plugin_definitions.get((plugin_key, plugin_contract_version))
        if plugin_definition is None:
            raise PermissionError("system capability plugin identity is not generated")
        if (str(intent.capability_key), str(intent.contract_version)) not in plugin_definition.allowed_capabilities:
            raise PermissionError("system capability is not declared by plugin")
        if intent.binding_snapshot != {"binding_id": binding_id, "binding_version": binding_version}:
            raise PermissionError("system capability binding snapshot mismatch")
        if intent.creator_authority != "WORKLINE_PLUGIN":
            raise PermissionError("system capability creator authority mismatch")
        if intent.authorization_policy != "PLUGIN_DECLARED_CAPABILITY":
            raise PermissionError("system capability authorization policy mismatch")
        expected_provider = {"provider_code": "RUNTIME", "profile": definition.admission}
        if intent.provider_snapshot != expected_provider:
            raise PermissionError("system capability runtime provider snapshot mismatch")
        execution_session_id = getattr(inbox, "execution_session_id", None)
        execution_work_item_id = getattr(work_item, "id", None)
        if not isinstance(execution_session_id, int):
            execution_session_id = getattr(session, "execution_session_id", None)
        if not isinstance(execution_session_id, int) or not isinstance(execution_work_item_id, int):
            raise TypeError("system capability requires execution session/work-item identity")
        return {
            "execution_session_id": execution_session_id,
            "execution_work_item_id": execution_work_item_id,
            "plugin_key": str(plugin_key),
            "plugin_contract_version": str(plugin_contract_version),
            "binding_id": binding_id,
            "binding_version": binding_version,
        }

    @staticmethod
    def _final_idempotency_key(ctx: Mapping[str, Any], intent: RuntimeIntent) -> str:
        session_id = getattr(ctx.get("session"), "id", None)
        work_item = ctx.get("work_item")
        work_item_id = getattr(work_item, "id", None)
        raw = (
            f"system-capability:{intent.capability_key}@{intent.contract_version}:"
            f"session:{session_id}:work-item:{work_item_id}:{intent.operation_key}"
        )
        if len(raw) <= 160:
            return raw
        digest = sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"{raw[:139]}:{digest}"

    @staticmethod
    def _correlation_id(ctx: Mapping[str, Any]) -> str:
        inbox = ctx.get("inbox")
        value = getattr(inbox, "correlation_id", None) or ctx.get("correlation_id") or ctx.get("trace_id")
        if not isinstance(value, str) or not value:
            raise ValueError("SYSTEM_CAPABILITY effect requires execution correlation")
        return value

    @staticmethod
    def _business_owner_key(ctx: Mapping[str, Any], intent: RuntimeIntent) -> str:
        session_id = getattr(ctx.get("session"), "id", None)
        binding = intent.binding_snapshot
        return (
            f"session:{session_id}:binding:{binding.get('binding_id')}:{binding.get('binding_version')}:"
            f"policy:{intent.authorization_policy}"
        )[:160]


system_capability_intent_service = SystemCapabilityIntentService()

__all__ = [
    "PreparedSystemCapabilityIntent",
    "SystemCapabilityIntentService",
    "system_capability_intent_service",
]
