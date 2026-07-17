"""SYSTEM_CAPABILITY intent 的固定快照、幂等键与执行准备。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult, IdempotencyGuard, idempotency_guard
from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition, SystemCapabilityMode
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class PreparedSystemCapabilityIntent:
    definition: SystemCapabilityDefinition
    request: BaseModel
    idempotency_key: str
    payload_hash: str
    claim_result: ClaimResult


class SystemCapabilityIntentService:
    """Runtime-owned EFFECT admission；不执行 handler，也不控制事务。"""

    def __init__(
        self,
        *,
        definitions: Mapping[tuple[str, str], SystemCapabilityDefinition] | None = None,
        idempotency_guard: IdempotencyGuard | Any = idempotency_guard,
    ) -> None:
        if definitions is None:
            from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX

            definitions = SYSTEM_CAPABILITY_INDEX
        self._definitions = dict(definitions)
        self._idempotency_guard = idempotency_guard

    async def prepare_and_claim(self, ctx: Mapping[str, Any], intent: RuntimeIntent) -> PreparedSystemCapabilityIntent:
        if intent.kind is not RuntimeIntentKind.SYSTEM_CAPABILITY:
            raise ValueError("intent must be SYSTEM_CAPABILITY")
        identity = (str(intent.capability_key), str(intent.contract_version))
        definition = self._definitions.get(identity)
        if definition is None:
            raise ValueError("system capability is not present in generated index")
        if definition.mode is not SystemCapabilityMode.EFFECT:
            raise ValueError("SYSTEM_CAPABILITY intent requires EFFECT definition")
        if intent.payload_hash != sha256_digest(intent.payload_json):
            raise ValueError("SYSTEM_CAPABILITY payload_hash mismatch")
        provider_profile = intent.provider_snapshot.get("profile")
        if provider_profile != definition.admission:
            raise PermissionError("system capability provider admission snapshot mismatch")
        try:
            request = definition.input_model.model_validate(intent.payload_json)
        except ValidationError as exc:
            raise ValueError("system capability typed payload validation failed") from exc

        final_key = self._final_idempotency_key(ctx, intent)
        correlation_id = self._correlation_id(ctx)
        claim_result = await self._idempotency_guard.claim_or_match(
            ctx["db"],
            provider_code=str(intent.provider_snapshot.get("provider_code") or "RUNTIME"),
            operation_kind="system_capability_effect",
            idempotency_key=final_key,
            request_hash=str(intent.payload_hash),
            execution_correlation_id=correlation_id,
            business_owner_key=self._business_owner_key(ctx, intent),
            now_ms=int(timezone.now_utc().timestamp() * 1000),
        )
        return PreparedSystemCapabilityIntent(
            definition=definition,
            request=request,
            idempotency_key=final_key,
            payload_hash=str(intent.payload_hash),
            claim_result=claim_result,
        )

    @staticmethod
    def _final_idempotency_key(ctx: Mapping[str, Any], intent: RuntimeIntent) -> str:
        session_id = getattr(ctx.get("session"), "id", None)
        work_item = ctx.get("work_item")
        work_item_id = getattr(work_item, "id", None) or getattr(ctx.get("inbox"), "execution_work_item_id", None)
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
