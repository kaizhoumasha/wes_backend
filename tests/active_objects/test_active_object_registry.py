"""ActiveObjectRegistry conflict policy tests."""

from __future__ import annotations

from datetime import timedelta

from src.utils.timezone import timezone


def test_active_object_registry_reports_single_active_owner() -> None:
    from src.app.active_objects.registry import ActiveObjectFact, ActiveObjectRegistry

    registry = ActiveObjectRegistry()
    result = registry.resolve(
        [
            ActiveObjectFact(object_code="BIN-1", owner_kind="conveyor", owner_code="Q-IN", evidence_ref="inbox:1"),
        ]
    )

    assert result.object_code == "BIN-1"
    assert result.status == "ACTIVE"
    assert result.owner_kind == "conveyor"
    assert result.reconciliation_required is False


def test_active_object_registry_marks_conflict_as_reconciling() -> None:
    from src.app.active_objects.registry import ActiveObjectFact, ActiveObjectRegistry

    registry = ActiveObjectRegistry()
    result = registry.resolve(
        [
            ActiveObjectFact(object_code="BIN-1", owner_kind="conveyor", owner_code="Q-IN", evidence_ref="inbox:1"),
            ActiveObjectFact(object_code="BIN-1", owner_kind="station", owner_code="SCAN1", evidence_ref="inbox:2"),
        ]
    )

    assert result.status == "RECONCILING"
    assert result.reconciliation_required is True
    assert result.conflict_policy == "MULTI_ACTIVE_OWNER"
    assert result.evidence_refs == ["inbox:1", "inbox:2"]


def test_active_object_registry_allows_transfer_to_conveyor_transient_window() -> None:
    """IN_TRANSFER + ON_CONVEYOR 在 transient_until 前是合法瞬态。"""

    from src.app.active_objects.registry import ActiveObjectFact, ActiveObjectRegistry

    now = timezone.now_for_db()
    registry = ActiveObjectRegistry()
    result = registry.resolve(
        [
            ActiveObjectFact(
                object_code="BIN-1",
                owner_kind="handling",
                owner_code="MOVE-1",
                evidence_ref="move:1",
                presence_type="IN_TRANSFER",
                transient_until=now + timedelta(seconds=30),
            ),
            ActiveObjectFact(
                object_code="BIN-1",
                owner_kind="conveyor",
                owner_code="Q-IN",
                evidence_ref="inbox:1",
                presence_type="ON_CONVEYOR",
                transient_until=now + timedelta(seconds=30),
            ),
        ],
        now=now,
    )

    assert result.status == "TRANSIENT"
    assert result.reconciliation_required is False
    assert result.conflict_policy == "TRANSIENT_TRANSFER_HANDOFF"


def test_active_object_registry_escalates_expired_transfer_transient_window() -> None:
    """合法瞬态超过 transient_until 后进入 RECONCILING。"""

    from src.app.active_objects.registry import ActiveObjectFact, ActiveObjectRegistry

    now = timezone.now_for_db()
    registry = ActiveObjectRegistry()
    result = registry.resolve(
        [
            ActiveObjectFact(
                object_code="BIN-1",
                owner_kind="handling",
                owner_code="MOVE-1",
                evidence_ref="move:1",
                presence_type="IN_TRANSFER",
                transient_until=now - timedelta(seconds=1),
            ),
            ActiveObjectFact(
                object_code="BIN-1",
                owner_kind="conveyor",
                owner_code="Q-IN",
                evidence_ref="inbox:1",
                presence_type="ON_CONVEYOR",
                transient_until=now - timedelta(seconds=1),
            ),
        ],
        now=now,
    )

    assert result.status == "RECONCILING"
    assert result.reconciliation_required is True
    assert result.conflict_policy == "TRANSIENT_WINDOW_EXPIRED"
