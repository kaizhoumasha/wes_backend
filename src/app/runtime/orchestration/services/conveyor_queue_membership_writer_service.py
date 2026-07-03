"""ConveyorQueueMembership DB-backed writer service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.repositories.conveyor_queue_membership_repository import (
    ConveyorQueueMembershipRepository,
    conveyor_queue_membership_repository,
)
from src.app.runtime.orchestration.services.conveyor_queue_writer import (
    ConveyorQueueMembershipSnapshot,
    ConveyorQueueWriteDecision,
    ConveyorQueueWriteDecisionKind,
    ConveyorQueueWriter,
    ConveyorQueueWriteRequest,
    conveyor_queue_writer,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ConveyorQueueMembershipWriteResult:
    """Conveyor queue membership 写入结果。"""

    membership: ConveyorQueueMembership
    decision: ConveyorQueueWriteDecision
    created: bool
    diagnostics: ConveyorQueueMembershipWriteDiagnostics


@dataclass(frozen=True, slots=True)
class ConveyorQueueMembershipWriteDiagnostics:
    """Conveyor queue membership 写入诊断。"""

    decision_kind: str
    decision_reason: str
    created: bool
    reused_existing_after_integrity_conflict: bool
    runtime_hold_required: bool
    reconciliation_required: bool
    membership_status: str | None


class ConveyorQueueWriteBlocked(Exception):
    """Queue writer policy 阻断写入。"""

    def __init__(self, decision: ConveyorQueueWriteDecision) -> None:
        super().__init__(f"conveyor queue write blocked: {decision.reason}")
        self.decision = decision


class ConveyorQueueMembershipWriterService(BaseService[ConveyorQueueMembership, ConveyorQueueMembershipRepository]):
    """将 ConveyorQueueWriter 纯策略落到 runtime DB active 投影。"""

    def __init__(
        self,
        *,
        repository: ConveyorQueueMembershipRepository = conveyor_queue_membership_repository,
        writer: ConveyorQueueWriter = conveyor_queue_writer,
    ) -> None:
        super().__init__(repository, enable_cache=False)
        self.writer = writer

    async def write_active(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        conveyor_code: str,
        queue_code: str,
        queue_role: str,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        declared_queue_codes: Iterable[str] | None = None,
        strict: bool = True,
        correlation_id: str | None = None,
        evidence_json: dict[str, Any] | None = None,
        entered_at_ms: int | None = None,
        auto_commit: bool = True,
    ) -> ConveyorQueueMembershipWriteResult:
        """写入 ACTIVE queue membership，并处理幂等、placeholder 和冲突。"""

        normalized = _NormalizedWriteInput(
            workline_id=workline_id,
            conveyor_code=_required_text(conveyor_code, "conveyor_code"),
            queue_code=_required_text(queue_code, "queue_code"),
            queue_role=_required_text(queue_role, "queue_role"),
            bin_code=_optional_text(bin_code),
            placeholder_key=_optional_text(placeholder_key),
            declared_queue_codes=_normalize_declared_queue_codes(declared_queue_codes),
            strict=strict,
            correlation_id=_optional_text(correlation_id),
            evidence_json=dict(evidence_json or {}),
            entered_at_ms=entered_at_ms if entered_at_ms is not None else _now_ms(),
        )
        if normalized.bin_code is None and normalized.placeholder_key is None:
            raise ValueError("bin_code 或 placeholder_key 至少需要一个")

        active_memberships = await self.repo.list_active_by_identity(
            db,
            workline_id=normalized.workline_id,
            bin_code=normalized.bin_code,
            placeholder_key=normalized.placeholder_key,
            for_update=True,
        )
        active_memberships = _sort_active_memberships_for_request(
            active_memberships,
            bin_code=normalized.bin_code,
            placeholder_key=normalized.placeholder_key,
        )
        decision = self.writer.plan_write(
            ConveyorQueueWriteRequest(
                workline_id=normalized.workline_id,
                queue_code=normalized.queue_code,
                bin_code=normalized.bin_code,
                placeholder_key=normalized.placeholder_key,
                declared_queue_codes=normalized.declared_queue_codes,
                strict=normalized.strict,
            ),
            active_memberships=[_snapshot(membership) for membership in active_memberships],
        )

        if decision.kind == ConveyorQueueWriteDecisionKind.BLOCKED:
            raise ConveyorQueueWriteBlocked(decision)
        if decision.kind == ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY:
            membership = _find_active_membership(
                active_memberships,
                bin_code=normalized.bin_code,
                placeholder_key=normalized.placeholder_key,
            )
            if membership is None:
                raise RuntimeError("ConveyorQueueWriter 返回幂等复用，但未找到 ACTIVE membership")
            return _write_result(membership, decision, created=False)
        if decision.kind == ConveyorQueueWriteDecisionKind.RESOLVE_PLACEHOLDER:
            membership, effective_decision = await self._resolve_placeholder(
                db,
                active_memberships=active_memberships,
                normalized=normalized,
                decision=decision,
            )
            await self._finish(db, membership, auto_commit=auto_commit)
            return _write_result(membership, effective_decision, created=False)
        if decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING:
            membership = await self._mark_reconciling(
                db,
                active_memberships=active_memberships,
                normalized=normalized,
                decision=decision,
            )
            await self._finish(db, membership, auto_commit=auto_commit)
            return _write_result(membership, decision, created=False)

        (
            membership,
            effective_decision,
            created,
            reused_existing_after_integrity_conflict,
        ) = await self._create_active_with_conflict_recheck(
            db,
            normalized=normalized,
            decision=decision,
        )
        await self._finish(db, membership, auto_commit=auto_commit)
        return _write_result(
            membership,
            effective_decision,
            created=created,
            reused_existing_after_integrity_conflict=reused_existing_after_integrity_conflict,
        )

    async def _create_active_with_conflict_recheck(
        self,
        db: AsyncSession,
        *,
        normalized: _NormalizedWriteInput,
        decision: ConveyorQueueWriteDecision,
    ) -> tuple[ConveyorQueueMembership, ConveyorQueueWriteDecision, bool, bool]:
        try:
            async with db.begin_nested():
                membership = await self.repo.create_without_session_rollback(
                    db,
                    {
                        "workline_id": normalized.workline_id,
                        "conveyor_code": normalized.conveyor_code,
                        "queue_code": normalized.queue_code,
                        "queue_role": normalized.queue_role,
                        "bin_code": normalized.bin_code,
                        "placeholder_key": normalized.placeholder_key,
                        "membership_status": "ACTIVE",
                        "entered_at": normalized.entered_at_ms,
                        "correlation_id": normalized.correlation_id,
                        "evidence_json": {
                            **normalized.evidence_json,
                            **_decision_evidence(decision),
                        },
                    },
                )
                return membership, decision, True, False
        except IntegrityError:
            active_memberships = await self._read_active_candidates_after_integrity_conflict(db, normalized=normalized)
            if not active_memberships:
                raise
            rechecked_decision = self.writer.plan_write(
                ConveyorQueueWriteRequest(
                    workline_id=normalized.workline_id,
                    queue_code=normalized.queue_code,
                    bin_code=normalized.bin_code,
                    placeholder_key=normalized.placeholder_key,
                    declared_queue_codes=normalized.declared_queue_codes,
                    strict=normalized.strict,
                ),
                active_memberships=[_snapshot(membership) for membership in active_memberships],
            )
            if rechecked_decision.kind == ConveyorQueueWriteDecisionKind.BLOCKED:
                raise ConveyorQueueWriteBlocked(rechecked_decision) from None
            if rechecked_decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING:
                membership = await self._mark_reconciling(
                    db,
                    active_memberships=active_memberships,
                    normalized=normalized,
                    decision=rechecked_decision,
                )
                return membership, rechecked_decision, False, True
            if rechecked_decision.kind == ConveyorQueueWriteDecisionKind.RESOLVE_PLACEHOLDER:
                membership, effective_decision = await self._resolve_placeholder(
                    db,
                    active_memberships=active_memberships,
                    normalized=normalized,
                    decision=rechecked_decision,
                )
                return membership, effective_decision, False, True
            existing = _find_active_membership(
                active_memberships,
                bin_code=normalized.bin_code,
                placeholder_key=normalized.placeholder_key,
            )
            if existing is None:
                raise
            return existing, rechecked_decision, False, True

    async def _resolve_placeholder(
        self,
        db: AsyncSession,
        *,
        active_memberships: list[ConveyorQueueMembership],
        normalized: _NormalizedWriteInput,
        decision: ConveyorQueueWriteDecision,
    ) -> tuple[ConveyorQueueMembership, ConveyorQueueWriteDecision]:
        placeholder = _find_placeholder_membership(active_memberships, placeholder_key=normalized.placeholder_key)
        if placeholder is None:
            raise RuntimeError("ConveyorQueueWriter 返回 placeholder resolve，但未找到 ACTIVE placeholder")
        placeholder_id = getattr(placeholder, "id", None)

        existing_bin = None
        if normalized.bin_code is not None:
            existing_bin = await self.repo.get_active_by_bin_code(
                db,
                workline_id=normalized.workline_id,
                bin_code=normalized.bin_code,
            )
        if existing_bin is not None and getattr(existing_bin, "id", None) != placeholder_id:
            return await self._mark_placeholder_resolve_conflict(
                db,
                placeholder=placeholder,
                existing_bin=existing_bin,
                normalized=normalized,
            )

        try:
            async with db.begin_nested():
                placeholder.bin_code = normalized.bin_code
                placeholder.placeholder_key = None
                placeholder.queue_code = normalized.queue_code
                placeholder.queue_role = normalized.queue_role
                placeholder.conveyor_code = normalized.conveyor_code
                placeholder.correlation_id = normalized.correlation_id or placeholder.correlation_id
                placeholder.evidence_json = _merge_evidence(
                    placeholder,
                    {
                        **normalized.evidence_json,
                        **_decision_evidence(decision),
                        "resolved_from_placeholder_key": normalized.placeholder_key,
                    },
                )
                db.add(placeholder)
                await db.flush()
        except IntegrityError:
            if normalized.bin_code is None:
                raise
            existing_bin = await self.repo.get_active_by_bin_code(
                db,
                workline_id=normalized.workline_id,
                bin_code=normalized.bin_code,
            )
            if existing_bin is None or getattr(existing_bin, "id", None) == placeholder_id:
                raise
            return await self._mark_placeholder_resolve_conflict(
                db,
                placeholder=placeholder,
                existing_bin=existing_bin,
                normalized=normalized,
            )
        return placeholder, decision

    async def _mark_placeholder_resolve_conflict(
        self,
        db: AsyncSession,
        *,
        placeholder: ConveyorQueueMembership,
        existing_bin: ConveyorQueueMembership,
        normalized: _NormalizedWriteInput,
    ) -> tuple[ConveyorQueueMembership, ConveyorQueueWriteDecision]:
        conflict_decision = _placeholder_resolve_conflict_decision(normalized)
        await db.refresh(placeholder)
        placeholder.membership_status = "RECONCILING"
        placeholder.bin_code = None
        placeholder.placeholder_key = normalized.placeholder_key
        placeholder.queue_code = normalized.queue_code
        placeholder.queue_role = normalized.queue_role
        placeholder.conveyor_code = normalized.conveyor_code
        placeholder.correlation_id = normalized.correlation_id or placeholder.correlation_id
        placeholder.evidence_json = _merge_evidence(
            placeholder,
            {
                **normalized.evidence_json,
                **_decision_evidence(conflict_decision),
                "resolved_from_placeholder_key": normalized.placeholder_key,
                "conflicting_bin_code": normalized.bin_code,
                "conflicting_membership_id": getattr(existing_bin, "id", None),
            },
        )
        db.add(placeholder)
        await db.flush()
        return placeholder, conflict_decision

    async def _mark_reconciling(
        self,
        db: AsyncSession,
        *,
        active_memberships: list[ConveyorQueueMembership],
        normalized: _NormalizedWriteInput,
        decision: ConveyorQueueWriteDecision,
    ) -> ConveyorQueueMembership:
        membership = _find_reconciling_membership(
            active_memberships,
            bin_code=normalized.bin_code,
            placeholder_key=normalized.placeholder_key,
            decision=decision,
        )
        if membership is None:
            raise RuntimeError("ConveyorQueueWriter 返回 RECONCILING，但未找到 ACTIVE membership")

        membership.membership_status = "RECONCILING"
        membership.evidence_json = _merge_evidence(
            membership,
            {
                **normalized.evidence_json,
                **_decision_evidence(decision),
                "existing_queue_code": membership.queue_code,
                "conflicting_queue_code": normalized.queue_code,
            },
        )
        db.add(membership)
        await db.flush()
        return membership

    async def _read_active_candidates_after_integrity_conflict(
        self,
        db: AsyncSession,
        *,
        normalized: _NormalizedWriteInput,
    ) -> list[ConveyorQueueMembership]:
        active_memberships = await self.repo.list_active_by_identity(
            db,
            workline_id=normalized.workline_id,
            bin_code=normalized.bin_code,
            placeholder_key=normalized.placeholder_key,
            for_update=True,
        )
        return _sort_active_memberships_for_request(
            active_memberships,
            bin_code=normalized.bin_code,
            placeholder_key=normalized.placeholder_key,
        )

    async def _finish(self, db: AsyncSession, membership: ConveyorQueueMembership, *, auto_commit: bool) -> None:
        await db.refresh(membership)
        if auto_commit:
            await self._commit_mutation(db)


@dataclass(frozen=True, slots=True)
class _NormalizedWriteInput:
    workline_id: int
    conveyor_code: str
    queue_code: str
    queue_role: str
    bin_code: str | None
    placeholder_key: str | None
    declared_queue_codes: frozenset[str]
    strict: bool
    correlation_id: str | None
    evidence_json: dict[str, Any]
    entered_at_ms: int


def _snapshot(membership: ConveyorQueueMembership) -> ConveyorQueueMembershipSnapshot:
    return ConveyorQueueMembershipSnapshot(
        workline_id=membership.workline_id,
        queue_code=membership.queue_code,
        bin_code=membership.bin_code,
        placeholder_key=membership.placeholder_key,
        membership_status=membership.membership_status,
    )


def _sort_active_memberships_for_request(
    memberships: list[ConveyorQueueMembership],
    *,
    bin_code: str | None,
    placeholder_key: str | None,
) -> list[ConveyorQueueMembership]:
    return sorted(
        memberships,
        key=lambda membership: (
            0 if bin_code is not None and membership.bin_code == bin_code else 1,
            0 if placeholder_key is not None and membership.placeholder_key == placeholder_key else 1,
            membership.id or 0,
        ),
    )


def _find_active_membership(
    memberships: list[ConveyorQueueMembership],
    *,
    bin_code: str | None,
    placeholder_key: str | None,
) -> ConveyorQueueMembership | None:
    if bin_code is not None:
        for membership in memberships:
            if membership.bin_code == bin_code:
                return membership
    if placeholder_key is not None:
        return _find_placeholder_membership(memberships, placeholder_key=placeholder_key)
    return None


def _find_reconciling_membership(
    memberships: list[ConveyorQueueMembership],
    *,
    bin_code: str | None,
    placeholder_key: str | None,
    decision: ConveyorQueueWriteDecision,
) -> ConveyorQueueMembership | None:
    if decision.reason == "ACTIVE_PLACEHOLDER_QUEUE_CONFLICT":
        return _find_placeholder_membership(memberships, placeholder_key=placeholder_key)
    if decision.reason == "ACTIVE_BIN_QUEUE_CONFLICT":
        return _find_bin_membership(memberships, bin_code=bin_code)
    return _find_active_membership(memberships, bin_code=bin_code, placeholder_key=placeholder_key)


def _find_bin_membership(
    memberships: list[ConveyorQueueMembership],
    *,
    bin_code: str | None,
) -> ConveyorQueueMembership | None:
    if bin_code is None:
        return None
    for membership in memberships:
        if membership.bin_code == bin_code:
            return membership
    return None


def _find_placeholder_membership(
    memberships: list[ConveyorQueueMembership],
    *,
    placeholder_key: str | None,
) -> ConveyorQueueMembership | None:
    if placeholder_key is None:
        return None
    for membership in memberships:
        if membership.placeholder_key == placeholder_key and membership.bin_code is None:
            return membership
    return None


def _decision_evidence(decision: ConveyorQueueWriteDecision) -> dict[str, Any]:
    return {
        "policy_decision": decision.kind.value,
        "policy_reason": decision.reason,
        "runtime_hold_required": decision.runtime_hold_required,
        "reconciliation_required": decision.reconciliation_required,
        "reuse_existing": decision.reuse_existing,
    }


def _placeholder_resolve_conflict_decision(normalized: _NormalizedWriteInput) -> ConveyorQueueWriteDecision:
    return ConveyorQueueWriteDecision(
        kind=ConveyorQueueWriteDecisionKind.RECONCILING,
        reason="ACTIVE_BIN_PLACEHOLDER_RESOLVE_CONFLICT",
        queue_code=normalized.queue_code,
        bin_code=normalized.bin_code,
        runtime_hold_required=True,
        reconciliation_required=True,
        reuse_existing=True,
    )


def _write_result(
    membership: ConveyorQueueMembership,
    decision: ConveyorQueueWriteDecision,
    *,
    created: bool,
    reused_existing_after_integrity_conflict: bool = False,
) -> ConveyorQueueMembershipWriteResult:
    return ConveyorQueueMembershipWriteResult(
        membership=membership,
        decision=decision,
        created=created,
        diagnostics=ConveyorQueueMembershipWriteDiagnostics(
            decision_kind=decision.kind.value,
            decision_reason=decision.reason,
            created=created,
            reused_existing_after_integrity_conflict=reused_existing_after_integrity_conflict,
            runtime_hold_required=decision.runtime_hold_required,
            reconciliation_required=decision.reconciliation_required,
            membership_status=membership.membership_status,
        ),
    )


def _merge_evidence(
    membership: ConveyorQueueMembership,
    evidence_json: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        **dict(getattr(membership, "evidence_json", None) or {}),
        **dict(evidence_json or {}),
    }


def _normalize_declared_queue_codes(values: Iterable[str] | None) -> frozenset[str]:
    return frozenset(normalized for value in values or () if (normalized := str(value).strip()))


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


conveyor_queue_membership_writer_service = ConveyorQueueMembershipWriterService()


__all__ = [
    "ConveyorQueueMembershipWriteDiagnostics",
    "ConveyorQueueMembershipWriteResult",
    "ConveyorQueueMembershipWriterService",
    "ConveyorQueueWriteBlocked",
    "conveyor_queue_membership_writer_service",
]
