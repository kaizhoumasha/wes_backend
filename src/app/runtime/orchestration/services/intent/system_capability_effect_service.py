"""SYSTEM_CAPABILITY EFFECT 的通用执行协调器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult, IdempotencyConflict
from src.app.runtime.system_capabilities.definition import EffectCompletionMode
from src.app.runtime.system_capabilities.outcomes import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
)

from .system_capability_intent_service import SystemCapabilityIntentService, system_capability_intent_service

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent

type EffectOutcome = Success[Any] | BusinessReject | RetryableFailure | ContractViolation


@dataclass(frozen=True, slots=True)
class SystemCapabilityExecution:
    """Handler 可见的最小事务上下文；不暴露 commit/rollback。"""

    ctx: dict[str, Any]
    intent: RuntimeIntent
    idempotency_key: str

    @property
    def db(self) -> Any:
        return self.ctx["db"]


@dataclass(frozen=True, slots=True)
class SystemCapabilityEffectResult:
    outcome: EffectOutcome
    completion_mode: EffectCompletionMode | None
    durably_accepted: bool = False
    remote_completed: bool = False
    idempotent_replay: bool = False
    retryable: bool = False


class SystemCapabilityEffectService:
    """外层事务参与者；只 flush，绝不 commit/rollback 或执行外部 I/O。"""

    def __init__(self, *, intent_service: SystemCapabilityIntentService = system_capability_intent_service) -> None:
        self._intent_service = intent_service

    async def apply(self, ctx: dict[str, Any], intent: RuntimeIntent) -> SystemCapabilityEffectResult:
        try:
            prepared = await self._intent_service.prepare_and_claim(ctx, intent)
        except IdempotencyConflict:
            return SystemCapabilityEffectResult(
                outcome=ContractViolation(
                    error_code="IDEMPOTENCY_CONFLICT",
                    message="same operation identity was claimed with a different payload",
                ),
                completion_mode=None,
            )
        except (KeyError, PermissionError, TypeError, ValidationError, ValueError):
            return SystemCapabilityEffectResult(
                outcome=ContractViolation(
                    error_code="CAPABILITY_CONTRACT_INVALID",
                    message="system capability intent failed closed",
                ),
                completion_mode=None,
            )

        definition = prepared.definition
        if prepared.claim_result is ClaimResult.MATCH:
            return SystemCapabilityEffectResult(
                outcome=Success(payload={"idempotent": True}),
                completion_mode=definition.completion_mode,
                durably_accepted=definition.completion_mode is EffectCompletionMode.OUTBOX_ASYNC,
                idempotent_replay=True,
            )

        execution = SystemCapabilityExecution(ctx=ctx, intent=intent, idempotency_key=prepared.idempotency_key)
        try:
            handler = definition.handler_factory()
            raw = await asyncio.wait_for(
                handler(prepared.request, execution=execution),
                timeout=min(float(intent.timeout_seconds or definition.timeout_seconds), definition.timeout_seconds),
            )
            outcome = self._normalize_outcome(raw, output_model=definition.output_model)
        except TimeoutError:
            outcome = RetryableFailure(error_code="TIMEOUT", message="system capability effect timed out")
        except Exception:
            outcome = RetryableFailure(error_code="UNKNOWN", message="system capability effect failed")

        if isinstance(outcome, Success):
            flush = getattr(ctx["db"], "flush", None)
            if callable(flush):
                await flush()
        return SystemCapabilityEffectResult(
            outcome=outcome,
            completion_mode=definition.completion_mode,
            durably_accepted=(
                isinstance(outcome, Success) and definition.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
            ),
            remote_completed=(
                isinstance(outcome, Success) and definition.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL
            ),
            retryable=isinstance(outcome, RetryableFailure),
        )

    @staticmethod
    def _normalize_outcome(raw: object, *, output_model: type[BaseModel]) -> EffectOutcome:
        if isinstance(raw, Success):
            try:
                return Success(payload=output_model.model_validate(raw.payload))
            except ValidationError:
                return ContractViolation(error_code="OUTPUT_CONTRACT_INVALID", message="success payload is invalid")
        if isinstance(raw, BusinessReject | RetryableFailure | ContractViolation):
            return raw
        try:
            return Success(payload=output_model.model_validate(raw))
        except ValidationError:
            return ContractViolation(error_code="OUTPUT_CONTRACT_INVALID", message="handler output is invalid")


system_capability_effect_service = SystemCapabilityEffectService()

__all__ = [
    "SystemCapabilityEffectResult",
    "SystemCapabilityEffectService",
    "SystemCapabilityExecution",
    "system_capability_effect_service",
]
