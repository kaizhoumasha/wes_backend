"""SYSTEM_CAPABILITY EFFECT 的通用执行协调器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from inspect import isawaitable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.exc import DBAPIError

from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult, IdempotencyConflict
from src.app.runtime.orchestration.system_capability_effect_claim import SystemCapabilityIdempotencyConflict
from src.app.runtime.system_capabilities.definition import EffectCompletionMode
from src.app.runtime.system_capabilities.outcomes import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
    parse_outcome,
)
from src.utils.timezone import timezone

from .system_capability_intent_service import SystemCapabilityIntentService, system_capability_intent_service

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog

type EffectOutcome = Success[Any] | BusinessReject | RetryableFailure | ContractViolation


@dataclass(frozen=True, slots=True)
class SystemCapabilityExecution:
    """Handler 可见的最小事务上下文；不暴露 commit/rollback。"""

    ctx: dict[str, Any]
    intent: RuntimeIntent
    admission: BaseModel
    idempotency_key: str
    intent_log: RuntimeIntentLog | None

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
    evidence: SystemCapabilityEffectEvidence | None = None


class SystemCapabilityEffectEvidence(BaseModel):
    """可持久化并供 Plugin 下一 attempt 消费的 typed outcome。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_key: str
    contract_version: str
    operation_key: str
    idempotency_key: str
    payload_hash: str
    outcome_kind: str
    outcome_code: str
    outcome: dict[str, Any]
    occurred_at_ms: int


class SystemCapabilityEffectService:
    """外层事务参与者；只 flush，绝不 commit/rollback 或执行外部 I/O。"""

    def __init__(self, *, intent_service: SystemCapabilityIntentService = system_capability_intent_service) -> None:
        self._intent_service = intent_service

    async def apply(self, ctx: dict[str, Any], intent: RuntimeIntent) -> SystemCapabilityEffectResult:
        try:
            prepared = await self._intent_service.prepare_and_claim(ctx, intent)
        except (IdempotencyConflict, SystemCapabilityIdempotencyConflict) as conflict:
            _ = await self._intent_service.record_idempotency_conflict(ctx, conflict=conflict)
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
        if prepared.claim_result is ClaimResult.MATCH or getattr(prepared.claim_result, "value", None) == "MATCH":
            replay = await self._replay_success(ctx, intent=intent, prepared=prepared)
            if replay is not None:
                outcome, evidence = replay
                return SystemCapabilityEffectResult(
                    outcome=outcome,
                    completion_mode=definition.completion_mode,
                    durably_accepted=definition.completion_mode is EffectCompletionMode.OUTBOX_ASYNC,
                    # 已通过校验的 terminal success evidence 表示远端完成；这与
                    # OUTBOX_ASYNC 的 PROPOSED/no-evidence durable acceptance 不同。
                    remote_completed=True,
                    idempotent_replay=True,
                    evidence=evidence,
                )
            if (
                definition.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
                and prepared.intent_log is not None
                and (
                    prepared.intent_log.effect_status is RuntimeIntentStatus.ACCEPTED
                    or (
                        prepared.intent_log.effect_status is RuntimeIntentStatus.PROPOSED
                        and prepared.has_durable_outbox
                    )
                )
            ):
                # 配对 outbox 的 PROPOSED 双账本或 transport ACCEPTED 均已 durable accepted；
                # 只重放接受语义，不伪造 payload/evidence 或再次执行 handler。
                return SystemCapabilityEffectResult(
                    outcome=Success(payload=None),
                    completion_mode=definition.completion_mode,
                    durably_accepted=True,
                    idempotent_replay=True,
                )
            provisional_without_outbox = (
                definition.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
                and prepared.intent_log is not None
                and prepared.intent_log.effect_status is RuntimeIntentStatus.PROPOSED
                and not prepared.has_durable_outbox
            )
            local_redecision = (
                definition.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL
                and prepared.intent_log is not None
                and prepared.intent_log.effect_status is RuntimeIntentStatus.PROPOSED
            )
            if not provisional_without_outbox and not local_redecision:
                return SystemCapabilityEffectResult(
                    outcome=ContractViolation(
                        error_code="PERSISTED_OUTCOME_INVALID",
                        message="persisted success evidence failed closed",
                    ),
                    completion_mode=definition.completion_mode,
                    idempotent_replay=True,
                )

        execution = SystemCapabilityExecution(
            ctx=ctx,
            intent=intent,
            admission=prepared.admission,
            idempotency_key=prepared.idempotency_key,
            intent_log=prepared.intent_log,
        )
        try:
            handler = definition.handler_factory()
            raw = await asyncio.wait_for(
                handler(prepared.request, execution=execution),
                timeout=min(float(intent.timeout_seconds or definition.timeout_seconds), definition.timeout_seconds),
            )
            outcome = self._normalize_outcome(raw, output_model=definition.output_model)
        except DBAPIError:
            # PostgreSQL 数据库异常会使当前事务失效；必须交由外层统一 rollback，
            # 不能继续使用同一会话写 RetryableFailure evidence。
            raise
        except TimeoutError:
            outcome = RetryableFailure(error_code="TIMEOUT", message="system capability effect timed out")
        except Exception:
            outcome = RetryableFailure(error_code="UNKNOWN", message="system capability effect failed")

        if isinstance(outcome, Success):
            flush = getattr(ctx["db"], "flush", None)
            if callable(flush):
                flush_result = flush()
                if isawaitable(flush_result):
                    await flush_result
        # OUTBOX_ASYNC 只证明同事务的 RuntimeIntentLog/SystemOutbox 已 durable accepted；
        # 不能把入队、transport SENT 或本次可重试错误写成 capability 终态。
        # 终态只由后续 transport/callback/reconciliation evidence 的 reducer 推进。
        evidence = None
        if definition.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL:
            evidence = self._build_evidence(intent=intent, prepared=prepared, outcome=outcome)
            await self._intent_service.record_outcome(ctx, prepared=prepared, evidence=evidence)
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
            evidence=evidence,
        )

    @staticmethod
    def _build_evidence(
        *, intent: RuntimeIntent, prepared: Any, outcome: EffectOutcome
    ) -> SystemCapabilityEffectEvidence:
        code = getattr(outcome, "reason_code", None) or getattr(outcome, "error_code", None) or "SUCCESS"
        return SystemCapabilityEffectEvidence(
            capability_key=str(intent.capability_key),
            contract_version=str(intent.contract_version),
            operation_key=str(intent.operation_key),
            idempotency_key=prepared.idempotency_key,
            payload_hash=prepared.payload_hash,
            outcome_kind=outcome.kind,
            outcome_code=str(code),
            outcome=outcome.model_dump(mode="json"),
            occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
        )

    async def _replay_success(
        self, ctx: dict[str, Any], *, intent: RuntimeIntent, prepared: Any
    ) -> tuple[Success[Any], SystemCapabilityEffectEvidence] | None:
        raw = await self._intent_service.get_success_evidence(ctx, prepared=prepared)
        try:
            evidence = SystemCapabilityEffectEvidence.model_validate(raw)
        except ValidationError:
            return None
        if (
            evidence.capability_key != str(intent.capability_key)
            or evidence.contract_version != str(intent.contract_version)
            or evidence.operation_key != str(intent.operation_key)
            or evidence.idempotency_key != prepared.idempotency_key
            or evidence.payload_hash != prepared.payload_hash
            or evidence.outcome_kind != "success"
        ):
            return None
        outcome = parse_outcome(evidence.outcome, payload_type=prepared.definition.output_model)
        if not isinstance(outcome, Success):
            return None
        return outcome, evidence

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
    "SystemCapabilityEffectEvidence",
    "SystemCapabilityEffectResult",
    "SystemCapabilityEffectService",
    "SystemCapabilityExecution",
    "system_capability_effect_service",
]
