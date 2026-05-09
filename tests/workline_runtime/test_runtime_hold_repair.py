from typing import cast

import pytest

from scripts.data.repair_runtime_holds import repair_runtime_holds
from src.app.workline.models import LineType, WorkLine
from src.app.workline.models.runtime_hold import RuntimeHold, RuntimeHoldStatus, RuntimeHoldType
from src.app.workline.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationSourceKind,
    RuntimeReconciliationState,
    SessionStatus,
    WorklineSession,
)
from src.app.workline.repositories.runtime_hold_repository import runtime_hold_repository

pytestmark = pytest.mark.asyncio


async def _create_workline(db_session, *, code: str) -> WorkLine:
    workline = WorkLine(line_code=code, line_name=code, line_type=LineType.AUTO)
    db_session.add(workline)
    await db_session.flush()
    return workline


async def _create_pending_reconciliation_session(
    db_session,
    workline: WorkLine,
    *,
    code: str,
    reason: str | None = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
    source_inbox_id: int | None = 101,
    source_outbox_id: int | None = None,
    command_id: int | None = None,
) -> WorklineSession:
    session = WorklineSession(
        session_code=code,
        workline_id=cast("int", workline.id),
        plugin_key="smt_classifier",
        contract_version="1.0",
        status=SessionStatus.MANUAL_HOLD,
        reconciliation_state=RuntimeReconciliationState.PENDING,
        reconciliation_reason=reason,  # type: ignore[arg-type]
        reconciliation_source_kind=(
            RuntimeReconciliationSourceKind.TIMER_TIMEOUT
            if reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value
            else RuntimeReconciliationSourceKind.DISPATCH_ACK_EXHAUSTED
        ),
        reconciliation_source_inbox_id=source_inbox_id,
        reconciliation_source_outbox_id=source_outbox_id,
        reconciliation_command_id=command_id,
        reconciliation_device_id=7,
        reconciliation_wait_token="CMD-REPAIR",
    )
    db_session.add(session)
    await db_session.flush()
    return session


async def test_runtime_hold_repair_dry_run_does_not_write(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-REPAIR-DRY")
    _ = await _create_pending_reconciliation_session(db_session, workline, code="S-REPAIR-DRY")

    summary = await repair_runtime_holds(db_session, apply=False, limit=100)

    assert summary["would_create"] == 1
    assert summary["created"] == 0
    assert summary["missing_material_identity"] == 1
    assert summary["active_reconciliation_sessions"] == 1
    assert summary["active_runtime_holds"] == 0


async def test_runtime_hold_repair_apply_is_idempotent(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-REPAIR-APPLY")
    session = await _create_pending_reconciliation_session(db_session, workline, code="S-REPAIR-APPLY")

    first = await repair_runtime_holds(db_session, apply=True, limit=100)
    second = await repair_runtime_holds(db_session, apply=True, limit=100)

    assert first["created"] == 1
    assert second["created"] == 0
    holds = await runtime_hold_repository.get_active_blocking_by_workline(db_session, cast("int", workline.id))
    assert len([hold for hold in holds if hold.session_id == session.id]) == 1
    assert second["active_reconciliation_sessions"] == 1
    assert second["active_runtime_holds"] == 1


async def test_runtime_hold_repair_counts_duplicate_source_key(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-REPAIR-DUP")
    session = await _create_pending_reconciliation_session(db_session, workline, code="S-REPAIR-DUP")
    db_session.add(
        RuntimeHold(
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            status=RuntimeHoldStatus.RESOLVED,
            workline_id=cast("int", workline.id),
            session_id=cast("int", session.id),
            plugin_key="smt_classifier",
            contract_version="1.0",
            source_kind="TIMER_TIMEOUT",
            source_reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
            source_idempotency_key=f"callback-timeout:{session.id}:101",
            source_inbox_id=101,
            evidence_snapshot_json={},
        )
    )
    await db_session.flush()

    summary = await repair_runtime_holds(db_session, apply=True, limit=100)

    assert summary["duplicates"] == 1
    assert summary["created"] == 0


async def test_runtime_hold_repair_counts_unmapped_reasons(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-REPAIR-UNKNOWN")
    _ = await _create_pending_reconciliation_session(
        db_session,
        workline,
        code="S-REPAIR-UNKNOWN",
        reason=None,
    )

    summary = await repair_runtime_holds(db_session, apply=True, limit=100)

    assert summary["unmapped_reasons"] == {"UNKNOWN": 1}
    assert summary["created"] == 0
