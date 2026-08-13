"""RuntimeInbox backpressure 与通用 conveyor queue 恢复策略。"""

from __future__ import annotations


def test_runtime_inbox_backpressure_enters_degraded_mode() -> None:
    """Inbox 积压超过阈值时进入降级模式，但不丢弃消息。"""

    from src.app.runtime.orchestration.services.inbox.backpressure import RuntimeInboxBackpressurePolicy

    decision = RuntimeInboxBackpressurePolicy(max_pending=100, dead_letter_threshold=10).evaluate(
        pending_count=125,
        dead_letter_count=3,
    )

    assert decision.mode == "DEGRADED"
    assert decision.accept_new_messages is True
    assert decision.dispatch_immediate_processing is False
    assert decision.reason == "PENDING_BACKLOG"


def test_runtime_inbox_backpressure_requires_operator_attention_on_dead_letters() -> None:
    """死信超过阈值时要求人工处理。"""

    from src.app.runtime.orchestration.services.inbox.backpressure import RuntimeInboxBackpressurePolicy

    decision = RuntimeInboxBackpressurePolicy(max_pending=100, dead_letter_threshold=10).evaluate(
        pending_count=20,
        dead_letter_count=12,
    )

    assert decision.mode == "OPERATOR_ATTENTION"
    assert decision.accept_new_messages is True
    assert decision.dispatch_immediate_processing is False
    assert decision.reason == "DEAD_LETTER_BACKLOG"


def test_conveyor_queue_writer_resolves_placeholder_and_escalates_conflict() -> None:
    """Queue writer 必须幂等处理 active 唯一和 placeholder resolve。"""

    from src.app.runtime.orchestration.services.conveyor_queue_writer import (
        ConveyorQueueMembershipSnapshot,
        ConveyorQueueWriteDecisionKind,
        ConveyorQueueWriter,
        ConveyorQueueWriteRequest,
    )

    writer = ConveyorQueueWriter()
    placeholder = ConveyorQueueMembershipSnapshot(workline_id=1, queue_code="Q-IN", placeholder_key="scan:001")
    resolve = writer.plan_write(
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-IN",
            bin_code="BIN-001",
            placeholder_key="scan:001",
            declared_queue_codes=frozenset({"Q-IN", "Q-OUT"}),
        ),
        active_memberships=[placeholder],
    )
    placeholder_replay = writer.plan_write(
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-IN",
            placeholder_key="scan:001",
            declared_queue_codes=frozenset({"Q-IN", "Q-OUT"}),
        ),
        active_memberships=[placeholder],
    )
    conflict = writer.plan_write(
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-OUT",
            bin_code="BIN-002",
            declared_queue_codes=frozenset({"Q-IN", "Q-OUT"}),
        ),
        active_memberships=[ConveyorQueueMembershipSnapshot(workline_id=1, queue_code="Q-IN", bin_code="BIN-002")],
    )
    resolve_with_existing_bin = writer.plan_write(
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-IN",
            bin_code="BIN-001",
            placeholder_key="scan:001",
            declared_queue_codes=frozenset({"Q-IN", "Q-OUT"}),
        ),
        active_memberships=[
            ConveyorQueueMembershipSnapshot(workline_id=1, queue_code="Q-IN", bin_code="BIN-001"),
            placeholder,
        ],
    )
    blocked = writer.plan_write(
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-OTU",
            bin_code="BIN-003",
            declared_queue_codes=frozenset({"Q-IN", "Q-OUT"}),
        ),
        active_memberships=[],
    )

    assert resolve.kind == ConveyorQueueWriteDecisionKind.RESOLVE_PLACEHOLDER
    assert placeholder_replay.kind == ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY
    assert placeholder_replay.reuse_existing is True
    assert conflict.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert conflict.runtime_hold_required is True
    assert resolve_with_existing_bin.kind == ConveyorQueueWriteDecisionKind.RESOLVE_PLACEHOLDER
    assert blocked.kind == ConveyorQueueWriteDecisionKind.BLOCKED
    assert blocked.reason == "UNKNOWN_QUEUE_CODE"
