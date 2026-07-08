"""ReconciliationManager minimal decision model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.services.idempotency_guard import (
    ClaimResult,
    IdempotencyGuard,
)
from src.app.runtime.orchestration.services.idempotency_guard import (
    idempotency_guard as default_idempotency_guard,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class ReconciliationSeverity(str, Enum):
    """RECONCILING 冲突升级级别。"""

    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ResolutionAction(str, Enum):
    """ReconciliationManager 允许产出的 owner-scoped resolution action。"""

    HOLD_OWNER = "HOLD_OWNER"
    FREEZE_PROJECTION = "FREEZE_PROJECTION"
    MANUAL_RESOLVE_REQUIRED = "MANUAL_RESOLVE_REQUIRED"


@dataclass(frozen=True, slots=True)
class ReconciliationConflictInput:
    """登记一个 RECONCILING 冲突所需的最小输入。"""

    owner_domain: str
    owner_kind: str
    owner_id: str
    conflict_kind: str
    reason: str
    evidence_refs: list[str]
    detected_at: datetime
    owner_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """ReconciliationManager 产出的决议，不直接修改 owner 状态。"""

    owner_domain: str
    owner_kind: str
    owner_id: str
    conflict_kind: str
    reason: str
    evidence_refs: list[str]
    detected_at: datetime
    status: str
    severity: ReconciliationSeverity
    action: ResolutionAction
    runtime_hold_required: bool
    allowed_next_effect_scope: dict[str, str]
    owner_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationRegistrationResult:
    """带幂等 claim 结果的 reconciliation 登记输出。"""

    decision: ReconciliationDecision
    claim_result: ClaimResult


class ReconciliationManager:
    """登记 RECONCILING 冲突并产出 owner-scoped decision。"""

    def __init__(self, idempotency_guard: IdempotencyGuard = default_idempotency_guard) -> None:
        self.idempotency_guard = idempotency_guard

    def register_conflict(self, conflict: ReconciliationConflictInput) -> ReconciliationDecision:
        action = (
            ResolutionAction.FREEZE_PROJECTION if conflict.owner_domain == "resource" else ResolutionAction.HOLD_OWNER
        )
        return ReconciliationDecision(
            owner_domain=conflict.owner_domain,
            owner_kind=conflict.owner_kind,
            owner_id=conflict.owner_id,
            conflict_kind=conflict.conflict_kind,
            reason=conflict.reason,
            evidence_refs=list(conflict.evidence_refs),
            detected_at=conflict.detected_at,
            status="PENDING",
            severity=ReconciliationSeverity.WARNING,
            action=action,
            runtime_hold_required=True,
            allowed_next_effect_scope={
                "owner_domain": conflict.owner_domain,
                "owner_kind": conflict.owner_kind,
                "owner_id": conflict.owner_id,
            },
            owner_snapshot=dict(conflict.owner_snapshot) if conflict.owner_snapshot is not None else None,
        )

    async def register_conflict_idempotent(
        self,
        db: AsyncSession,
        conflict: ReconciliationConflictInput,
        *,
        provider_code: str,
        idempotency_key: str,
        request_hash: str,
        execution_correlation_id: str,
        now_ms: int,
        business_owner_key: str | None = None,
    ) -> ReconciliationRegistrationResult:
        """先 claim reconciliation 幂等键，再产出 owner-scoped decision。"""

        claim_result = await self.idempotency_guard.claim_or_match(
            db,
            provider_code=provider_code,
            operation_kind="reconciliation",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            execution_correlation_id=execution_correlation_id,
            now_ms=now_ms,
            business_owner_key=business_owner_key,
        )
        return ReconciliationRegistrationResult(
            decision=self.register_conflict(conflict),
            claim_result=claim_result,
        )

    def escalate(self, decision: ReconciliationDecision, *, now: datetime) -> ReconciliationDecision:
        age_seconds = (now - decision.detected_at).total_seconds()
        if age_seconds >= 30 * 60:
            severity = ReconciliationSeverity.CRITICAL
            action = ResolutionAction.MANUAL_RESOLVE_REQUIRED
        elif age_seconds >= 5 * 60:
            severity = ReconciliationSeverity.ERROR
            action = decision.action
        else:
            severity = decision.severity
            action = decision.action
        return replace(decision, severity=severity, action=action)
