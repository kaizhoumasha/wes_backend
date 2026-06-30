"""Repair historical pending runtime reconciliation sessions into RuntimeHold rows."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select

from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldStatus, RuntimeHoldType
from src.app.runtime.orchestration.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationSourceKind,
    RuntimeReconciliationState,
    WorklineSession,
)
from src.app.runtime.orchestration.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.workline.domain.material_identity import MaterialIdentityInput, MaterialIdentityResolutionStatus
from src.database.db import close_db, get_db_context, init_db
from src.workline_plugin_registry import get_workline_plugin_definition

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class RuntimeHoldRepairSummary:
    """Runtime Hold repair summary."""

    would_create: int = 0
    created: int = 0
    duplicates: int = 0
    unmapped_reasons: dict[str, int] = field(default_factory=dict)
    missing_material_identity: int = 0
    active_reconciliation_sessions: int = 0
    active_runtime_holds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_KNOWN_REASONS = {
    RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
    RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
    RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value,
}


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(cast("dict[str, Any]", value)) if isinstance(value, dict) else {}


def _source_kind(session: WorklineSession, reason: str) -> str:
    source_kind = _enum_value(session.reconciliation_source_kind)
    if source_kind:
        return source_kind
    if reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value:
        return RuntimeReconciliationSourceKind.TIMER_TIMEOUT.value
    return RuntimeReconciliationSourceKind.DISPATCH_ACK_EXHAUSTED.value


def _source_idempotency_key(session: WorklineSession, reason: str) -> str:
    session_id = cast("int", session.id)
    if reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value:
        inbox_key = session.reconciliation_source_inbox_id or "no-inbox"
        return f"callback-timeout:{session_id}:{inbox_key}"
    if reason in {
        RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
        RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value,
    }:
        outbox_key = session.reconciliation_source_outbox_id or "no-outbox"
        command_key = session.reconciliation_command_id or "no-command"
        return f"dispatch-ack-exhausted:{outbox_key}:{command_key}"
    return f"runtime-reconciliation:{session_id}:{reason}"


def _evidence_snapshot(session: WorklineSession, reason: str) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "reason": reason,
        "source_inbox_id": session.reconciliation_source_inbox_id,
        "source_outbox_id": session.reconciliation_source_outbox_id,
        "command_id": session.reconciliation_command_id,
        "device_id": session.reconciliation_device_id,
        "wait_token": session.reconciliation_wait_token,
        "ack_received_at": (
            session.reconciliation_ack_received_at.isoformat() if session.reconciliation_ack_received_at else None
        ),
        "deadline_at": session.reconciliation_deadline_at.isoformat() if session.reconciliation_deadline_at else None,
        "repaired_from_session": True,
    }


def _material_identity_missing(session: WorklineSession, evidence: dict[str, Any]) -> bool:
    definition = get_workline_plugin_definition(session.plugin_key)
    if definition is None:
        return True
    identity = definition.manifest.resolve_material_identity(
        MaterialIdentityInput(
            session_context=_as_dict(session.context_json),
            source_payload=evidence,
            plugin_context={"plugin_key": session.plugin_key, "contract_version": session.contract_version},
        )
    )
    return identity.resolution_status != MaterialIdentityResolutionStatus.RESOLVED


async def _pending_sessions_without_runtime_hold(db: AsyncSession, *, limit: int) -> list[WorklineSession]:
    session_columns = cast("Any", WorklineSession).__table__.c
    result = await db.execute(
        select(WorklineSession)
        .where(session_columns.reconciliation_state == RuntimeReconciliationState.PENDING)
        .order_by(session_columns.reconciliation_occurred_at.asc(), session_columns.id.asc())
        .limit(limit)
    )
    sessions = list(result.scalars().all())
    candidates: list[WorklineSession] = []
    for session in sessions:
        existing_holds = await runtime_hold_repository.get_active_blocking_by_workline(db, session.workline_id)
        if any(
            hold.session_id == session.id and hold.hold_type == RuntimeHoldType.RUNTIME_RECONCILIATION
            for hold in existing_holds
        ):
            continue
        candidates.append(session)
    return candidates


async def _active_reconciliation_session_count(db: AsyncSession) -> int:
    columns = cast("Any", WorklineSession).__table__.c
    result = await db.execute(
        select(func.count(columns.id)).where(columns.reconciliation_state == RuntimeReconciliationState.PENDING)
    )
    return int(result.scalar_one() or 0)


async def _active_runtime_hold_count(db: AsyncSession) -> int:
    from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold

    columns = cast("Any", RuntimeHold).__table__.c
    result = await db.execute(
        select(func.count(columns.id)).where(
            columns.hold_type == RuntimeHoldType.RUNTIME_RECONCILIATION,
            columns.status.in_(
                [
                    RuntimeHoldStatus.OPEN,
                    RuntimeHoldStatus.IN_PROGRESS,
                    RuntimeHoldStatus.REOPENED,
                ]
            ),
            columns.blocking.is_(True),
        )
    )
    return int(result.scalar_one() or 0)


async def repair_runtime_holds(
    db: AsyncSession,
    *,
    apply: bool,
    limit: int = 100,
) -> dict[str, Any]:
    """Repair missing RuntimeHold rows for pending reconciliation sessions."""

    summary = RuntimeHoldRepairSummary()
    unmapped = Counter[str]()
    sessions = await _pending_sessions_without_runtime_hold(db, limit=limit)

    for session in sessions:
        reason = _enum_value(session.reconciliation_reason) or "UNKNOWN"
        if reason not in _KNOWN_REASONS:
            unmapped[reason] += 1
            continue

        source_key = _source_idempotency_key(session, reason)
        existing = await runtime_hold_repository.get_by_source_idempotency_key(db, source_key)
        if existing is not None:
            summary.duplicates += 1
            continue

        evidence = _evidence_snapshot(session, reason)
        if _material_identity_missing(session, evidence):
            summary.missing_material_identity += 1

        summary.would_create += 1
        if not apply:
            continue

        _ = await runtime_hold_repository.create_open_hold(
            db,
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            workline_id=session.workline_id,
            session_id=session.id,
            trace_id=session.trace_id,
            plugin_key=session.plugin_key,
            contract_version=session.contract_version,
            source_kind=_source_kind(session, reason),
            source_reason=reason,
            source_idempotency_key=source_key,
            source_inbox_id=session.reconciliation_source_inbox_id,
            source_outbox_id=session.reconciliation_source_outbox_id,
            source_command_id=session.reconciliation_command_id,
            source_device_id=session.reconciliation_device_id,
            evidence_snapshot_json=evidence,
        )
        summary.created += 1

    summary.unmapped_reasons = dict(unmapped)
    summary.active_reconciliation_sessions = await _active_reconciliation_session_count(db)
    summary.active_runtime_holds = await _active_runtime_hold_count(db)
    await db.flush()
    return summary.to_dict()


async def _amain() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview repair without writing RuntimeHold rows")
    mode.add_argument("--apply", action="store_true", help="Apply repair")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    await init_db()
    try:
        async with get_db_context() as db:
            summary = await repair_runtime_holds(db, apply=args.apply, limit=args.limit)
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    finally:
        await close_db()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
