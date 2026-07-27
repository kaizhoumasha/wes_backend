"""ReconciliationManager contract tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.utils.timezone import timezone
from tests.support.runtime_binding import binding_pin_fields

NOW_MS = 1_700_000_000_000


async def _seed_execution_correlation(db_session, *, correlation_id: str = "corr-reconciliation-001"):
    """建立 ExecutionSession + ExecutionCorrelation，满足 IdempotencyKey FK 前置。"""

    session = ExecutionSession(
        workline_id=1,
        plugin_key="test-plugin",
        manifest_version="v1",
        **binding_pin_fields(),
        state="RUNNING",
    )
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=session.id,
        trace_id=f"trace-{correlation_id}",
    )
    db_session.add(correlation)
    await db_session.flush()
    return correlation


def test_reconciliation_manager_registers_owner_scoped_decision_without_owner_mutation() -> None:
    """RECONCILING 只能产出 owner-scoped decision, 不直接改 owner 状态。"""

    from src.app.reconciliation.manager import (
        ReconciliationConflictInput,
        ReconciliationManager,
        ReconciliationSeverity,
        ResolutionAction,
    )

    owner = {"state": "RUNNING"}
    manager = ReconciliationManager()

    decision = manager.register_conflict(
        ReconciliationConflictInput(
            owner_domain="runtime",
            owner_kind="ExecutionSession",
            owner_id="session-1001",
            conflict_kind="LATE_CALLBACK_AFTER_TIMEOUT",
            reason="callback deadline expired before result arrived",
            evidence_refs=["inbox:9001", "command:CMD-9001"],
            detected_at=timezone.now_for_db(),
            owner_snapshot=owner,
        )
    )

    assert decision.owner_domain == "runtime"
    assert decision.owner_id == "session-1001"
    assert decision.status == "PENDING"
    assert decision.severity == ReconciliationSeverity.WARNING
    assert decision.action == ResolutionAction.HOLD_OWNER
    assert decision.runtime_hold_required is True
    assert decision.allowed_next_effect_scope == {
        "owner_domain": "runtime",
        "owner_kind": "ExecutionSession",
        "owner_id": "session-1001",
    }
    assert decision.evidence_refs == ["inbox:9001", "command:CMD-9001"]
    assert owner == {"state": "RUNNING"}


def test_reconciliation_manager_escalates_after_5_and_30_minutes() -> None:
    """RECONCILING 冲突 5 分钟升级 warning, 30 分钟升级 critical。"""

    from src.app.reconciliation.manager import (
        ReconciliationConflictInput,
        ReconciliationManager,
        ReconciliationSeverity,
    )

    now = timezone.now_for_db()
    manager = ReconciliationManager()
    decision = manager.register_conflict(
        ReconciliationConflictInput(
            owner_domain="resource",
            owner_kind="ActiveProjection",
            owner_id="bin:BIN-001",
            conflict_kind="ACTIVE_OWNER_CONFLICT",
            reason="same bin appears in conveyor and station",
            evidence_refs=["projection:conveyor", "projection:station"],
            detected_at=now,
        )
    )

    after_4m = manager.escalate(decision, now=now + timedelta(minutes=4, seconds=59))
    after_5m = manager.escalate(decision, now=now + timedelta(minutes=5))
    after_30m = manager.escalate(decision, now=now + timedelta(minutes=30))

    assert after_4m.severity == ReconciliationSeverity.WARNING
    assert after_5m.severity == ReconciliationSeverity.ERROR
    assert after_30m.severity == ReconciliationSeverity.CRITICAL


def test_reconciliation_manager_marks_projection_conflict_as_freeze_and_hold() -> None:
    """active projection 冲突必须冻结投影并保留 RuntimeHold 隔离语义。"""

    from src.app.reconciliation.manager import (
        ReconciliationConflictInput,
        ReconciliationManager,
        ResolutionAction,
    )

    manager = ReconciliationManager()
    decision = manager.register_conflict(
        ReconciliationConflictInput(
            owner_domain="resource",
            owner_kind="ActiveObjectRegistry",
            owner_id="bin:BIN-001",
            conflict_kind="ACTIVE_OWNER_CONFLICT",
            reason="same bin appears in two active projections",
            evidence_refs=["registry:BIN-001"],
            detected_at=timezone.now_for_db(),
        )
    )

    assert decision.action == ResolutionAction.FREEZE_PROJECTION
    assert decision.runtime_hold_required is True
    assert decision.allowed_next_effect_scope["owner_id"] == "bin:BIN-001"


@pytest.mark.asyncio
async def test_reconciliation_manager_idempotent_register_claims_key_and_replays_same_hash(db_session) -> None:
    """reconciliation 生产入口必须先 claim 幂等键，同 hash 重放返回 MATCH。"""

    from src.app.reconciliation.manager import (
        ReconciliationConflictInput,
        ReconciliationManager,
    )
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult

    correlation = await _seed_execution_correlation(db_session)
    conflict = ReconciliationConflictInput(
        owner_domain="runtime",
        owner_kind="ExecutionSession",
        owner_id="session-1001",
        conflict_kind="DISPATCH_ACK_EXHAUSTED",
        reason="device command ACK exhausted",
        evidence_refs=["outbox:7001", "command:CMD-7001"],
        detected_at=timezone.now_for_db(),
    )
    manager = ReconciliationManager()

    first = await manager.register_conflict_idempotent(
        db_session,
        conflict,
        provider_code="WES",
        idempotency_key="WES-RECONCILIATION-hash001",
        request_hash="sha256-reconciliation-001",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
        business_owner_key="runtime:ExecutionSession:session-1001",
    )
    second = await manager.register_conflict_idempotent(
        db_session,
        conflict,
        provider_code="WES",
        idempotency_key="WES-RECONCILIATION-hash001",
        request_hash="sha256-reconciliation-001",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
        business_owner_key="runtime:ExecutionSession:session-1001",
    )

    assert first.claim_result is ClaimResult.NEW
    assert second.claim_result is ClaimResult.MATCH
    assert second.decision.allowed_next_effect_scope == first.decision.allowed_next_effect_scope
    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.provider_code == "WES",
                IdempotencyKey.operation_kind == "reconciliation",
                IdempotencyKey.idempotency_key == "WES-RECONCILIATION-hash001",
            )
        )
    ).scalar_one()
    assert stored.request_hash == "sha256-reconciliation-001"
    assert stored.business_owner_key == "runtime:ExecutionSession:session-1001"


@pytest.mark.asyncio
async def test_reconciliation_manager_idempotent_register_rejects_same_key_different_hash(db_session) -> None:
    """reconciliation 同 key 不同 hash 必须 409 并暴露审计 payload。"""

    from src.app.reconciliation.manager import (
        ReconciliationConflictInput,
        ReconciliationManager,
    )
    from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-reconciliation-conflict")
    conflict = ReconciliationConflictInput(
        owner_domain="runtime",
        owner_kind="ExecutionSession",
        owner_id="session-1002",
        conflict_kind="CALLBACK_DEADLINE_EXPIRED",
        reason="callback deadline expired",
        evidence_refs=["inbox:8001"],
        detected_at=timezone.now_for_db(),
    )
    manager = ReconciliationManager()

    await manager.register_conflict_idempotent(
        db_session,
        conflict,
        provider_code="WES",
        idempotency_key="WES-RECONCILIATION-conflict",
        request_hash="sha256-original",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
        business_owner_key="runtime:ExecutionSession:session-1002",
    )

    with pytest.raises(IdempotencyConflict) as exc_info:
        await manager.register_conflict_idempotent(
            db_session,
            conflict,
            provider_code="WES",
            idempotency_key="WES-RECONCILIATION-conflict",
            request_hash="sha256-tampered",
            execution_correlation_id=correlation.correlation_id,
            now_ms=NOW_MS,
            business_owner_key="runtime:ExecutionSession:session-1002",
        )

    assert exc_info.value.status_code == 409
    audit_event = exc_info.value.to_audit_event()
    assert audit_event["normalized_operation_kind"] == "reconciliation"
    assert audit_event["domain"] == "reconciliation"
    assert audit_event["incoming_request_hash"] == "sha256-tampered"
