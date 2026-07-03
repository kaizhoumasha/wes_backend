"""Phase 3 ConveyorQueueMembership writer policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConveyorQueueWriteDecisionKind(str, Enum):
    """Conveyor queue writer decision kind."""

    CREATE_ACTIVE = "CREATE_ACTIVE"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    RESOLVE_PLACEHOLDER = "RESOLVE_PLACEHOLDER"
    RECONCILING = "RECONCILING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ConveyorQueueMembershipSnapshot:
    """Active queue membership snapshot."""

    workline_id: int
    queue_code: str
    bin_code: str | None = None
    placeholder_key: str | None = None
    membership_status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class ConveyorQueueWriteRequest:
    """Queue membership write request."""

    workline_id: int
    queue_code: str
    bin_code: str | None = None
    placeholder_key: str | None = None
    declared_queue_codes: frozenset[str] = frozenset()
    strict: bool = True


@dataclass(frozen=True, slots=True)
class ConveyorQueueWriteDecision:
    """Queue writer decision result."""

    kind: ConveyorQueueWriteDecisionKind
    reason: str
    queue_code: str
    bin_code: str | None
    runtime_hold_required: bool = False
    reconciliation_required: bool = False
    reuse_existing: bool = False


class ConveyorQueueWriter:
    """Pure policy for active queue membership writes.

    The DB repository should use this decision before insert/upsert. It keeps
    SQLite unit tests deterministic while preserving the PostgreSQL semantics
    required by the Phase 3 gate.
    """

    def plan_write(
        self,
        request: ConveyorQueueWriteRequest,
        *,
        active_memberships: list[ConveyorQueueMembershipSnapshot],
    ) -> ConveyorQueueWriteDecision:
        if request.strict and request.queue_code not in request.declared_queue_codes:
            return ConveyorQueueWriteDecision(
                kind=ConveyorQueueWriteDecisionKind.BLOCKED,
                reason="UNKNOWN_QUEUE_CODE",
                queue_code=request.queue_code,
                bin_code=request.bin_code,
                runtime_hold_required=True,
                reconciliation_required=True,
            )

        for membership in active_memberships:
            if membership.workline_id != request.workline_id or membership.membership_status != "ACTIVE":
                continue
            if request.bin_code and membership.bin_code == request.bin_code:
                if membership.queue_code == request.queue_code:
                    return ConveyorQueueWriteDecision(
                        kind=ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY,
                        reason="ACTIVE_BIN_ALREADY_IN_QUEUE",
                        queue_code=request.queue_code,
                        bin_code=request.bin_code,
                        reuse_existing=True,
                    )
                return ConveyorQueueWriteDecision(
                    kind=ConveyorQueueWriteDecisionKind.RECONCILING,
                    reason="ACTIVE_BIN_QUEUE_CONFLICT",
                    queue_code=request.queue_code,
                    bin_code=request.bin_code,
                    runtime_hold_required=True,
                    reconciliation_required=True,
                    reuse_existing=True,
                )
            if (
                request.placeholder_key
                and membership.placeholder_key == request.placeholder_key
                and membership.bin_code is None
            ):
                if request.bin_code is None:
                    if membership.queue_code == request.queue_code:
                        return ConveyorQueueWriteDecision(
                            kind=ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY,
                            reason="ACTIVE_PLACEHOLDER_ALREADY_IN_QUEUE",
                            queue_code=request.queue_code,
                            bin_code=request.bin_code,
                            reuse_existing=True,
                        )
                    return ConveyorQueueWriteDecision(
                        kind=ConveyorQueueWriteDecisionKind.RECONCILING,
                        reason="ACTIVE_PLACEHOLDER_QUEUE_CONFLICT",
                        queue_code=request.queue_code,
                        bin_code=request.bin_code,
                        runtime_hold_required=True,
                        reconciliation_required=True,
                        reuse_existing=True,
                    )
                return ConveyorQueueWriteDecision(
                    kind=ConveyorQueueWriteDecisionKind.RESOLVE_PLACEHOLDER,
                    reason="PLACEHOLDER_RESOLVE",
                    queue_code=request.queue_code,
                    bin_code=request.bin_code,
                    reuse_existing=True,
                )

        return ConveyorQueueWriteDecision(
            kind=ConveyorQueueWriteDecisionKind.CREATE_ACTIVE,
            reason="CREATE_NEW_ACTIVE_MEMBERSHIP",
            queue_code=request.queue_code,
            bin_code=request.bin_code,
        )


conveyor_queue_writer = ConveyorQueueWriter()


__all__ = [
    "ConveyorQueueMembershipSnapshot",
    "ConveyorQueueWriteDecision",
    "ConveyorQueueWriteDecisionKind",
    "ConveyorQueueWriteRequest",
    "ConveyorQueueWriter",
    "conveyor_queue_writer",
]
