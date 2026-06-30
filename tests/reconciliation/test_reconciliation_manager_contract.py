"""Phase 3 ReconciliationManager contract tests."""

from __future__ import annotations

from datetime import timedelta

from src.utils.timezone import timezone


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
    """Phase 3: RECONCILING 冲突 5 分钟升级 warning, 30 分钟升级 critical。"""

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
