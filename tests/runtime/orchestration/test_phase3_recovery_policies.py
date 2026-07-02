"""Phase 3 RuntimeInbox backpressure and DeviceCommand lease policies."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from src.utils.timezone import timezone


def test_runtime_inbox_backpressure_enters_degraded_mode() -> None:
    """Inbox 积压超过阈值时进入降级模式, 但不丢弃消息。"""

    from src.app.runtime.orchestration.services.inbox.backpressure import (
        RuntimeInboxBackpressurePolicy,
    )

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

    from src.app.runtime.orchestration.services.inbox.backpressure import (
        RuntimeInboxBackpressurePolicy,
    )

    decision = RuntimeInboxBackpressurePolicy(max_pending=100, dead_letter_threshold=10).evaluate(
        pending_count=20,
        dead_letter_count=12,
    )

    assert decision.mode == "OPERATOR_ATTENTION"
    assert decision.accept_new_messages is True
    assert decision.dispatch_immediate_processing is False
    assert decision.reason == "DEAD_LETTER_BACKLOG"


def test_device_command_lease_expires_and_allows_replay() -> None:
    """DeviceCommand lease 到期后允许重放/取消, 未到期不允许。"""

    from src.app.runtime.orchestration.services.device_command_lease import (
        DeviceCommandLease,
        DeviceCommandLeasePolicy,
    )

    policy = DeviceCommandLeasePolicy(default_lease_seconds=30)
    active = DeviceCommandLease(command_code="CMD-1", device_code="DEV-1", leased_at=100, lease_seconds=30)

    assert policy.evaluate(active, now=129).expired is False
    expired = policy.evaluate(active, now=130)
    assert expired.expired is True
    assert expired.replay_allowed is True
    assert expired.cancel_allowed is True
    assert expired.reason == "LEASE_EXPIRED"


def test_device_command_lease_policy_accepts_device_command_snapshot() -> None:
    """DeviceCommand 模型快照可直接判定 lease, 避免调用方重复组装 dataclass。"""

    from src.app.runtime.orchestration.services.device_command_lease import (
        DeviceCommandLeasePolicy,
    )

    now = timezone.now_for_db()
    command = SimpleNamespace(
        command_code="CMD-2",
        sent_at=now - timedelta(seconds=31),
        timeout_ms=30_000,
    )

    decision = DeviceCommandLeasePolicy(default_lease_seconds=60).evaluate_command(command, now=now)

    assert decision.expired is True
    assert decision.replay_allowed is True
    assert decision.cancel_allowed is True
    assert decision.reason == "LEASE_EXPIRED"


def test_device_dispatch_policy_allows_only_fresh_idle_snapshot() -> None:
    """派发前必须有未过期 IDLE 快照。"""

    from src.app.runtime.orchestration.services.device_dispatch_policy import (
        DeviceDispatchDecisionKind,
        DeviceDispatchPolicy,
        DeviceDispatchRequest,
        DeviceRuntimeSnapshot,
        DeviceRuntimeStatus,
    )

    now = timezone.now_for_db()
    request = DeviceDispatchRequest(
        command_code="CMD-1",
        device_role="scanner",
        capability_code="SCAN",
        dispatch_deadline_at=now + timedelta(seconds=5),
    )
    snapshot = DeviceRuntimeSnapshot(
        device_code="DEV-1",
        status=DeviceRuntimeStatus.IDLE,
        observed_at=now,
        status_valid_until=now + timedelta(milliseconds=1000),
    )

    decision = DeviceDispatchPolicy().evaluate(request, snapshot=snapshot, now=now)

    assert decision.kind == DeviceDispatchDecisionKind.ALLOW_DISPATCH
    assert decision.dispatch_allowed is True
    assert decision.runtime_hold_required is False


def test_device_dispatch_policy_retries_stale_or_unknown_status_then_holds() -> None:
    """过期/UNKNOWN 快照只能重查 ECS, 退避耗尽后 RuntimeHold。"""

    from src.app.runtime.orchestration.services.device_dispatch_policy import (
        DeviceDispatchDecisionKind,
        DeviceDispatchPolicy,
        DeviceDispatchRequest,
        DeviceRuntimeSnapshot,
        DeviceRuntimeStatus,
    )

    now = timezone.now_for_db()
    policy = DeviceDispatchPolicy(retry_delays_seconds=(1, 2, 4))
    stale_request = DeviceDispatchRequest(
        command_code="CMD-2",
        device_role="scanner",
        capability_code="SCAN",
        dispatch_deadline_at=now + timedelta(seconds=5),
        retry_attempt=0,
    )
    stale_snapshot = DeviceRuntimeSnapshot(
        device_code="DEV-2",
        status=DeviceRuntimeStatus.IDLE,
        observed_at=now - timedelta(seconds=2),
        status_valid_until=now - timedelta(milliseconds=1),
    )

    retry = policy.evaluate(stale_request, snapshot=stale_snapshot, now=now)
    exhausted = policy.evaluate(
        DeviceDispatchRequest(
            command_code="CMD-2",
            device_role="scanner",
            capability_code="SCAN",
            dispatch_deadline_at=now + timedelta(seconds=5),
            retry_attempt=3,
        ),
        snapshot=DeviceRuntimeSnapshot(
            device_code="DEV-2",
            status=DeviceRuntimeStatus.UNKNOWN,
            observed_at=now,
            status_valid_until=now + timedelta(milliseconds=1000),
        ),
        now=now,
    )

    assert retry.kind == DeviceDispatchDecisionKind.RETRY_STATUS_PROBE
    assert retry.retry_after_seconds == 1
    assert exhausted.kind == DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD
    assert exhausted.runtime_hold_required is True
    assert exhausted.reason == "DEVICE_UNKNOWN_RETRY_EXHAUSTED"


def test_device_dispatch_policy_bounds_running_wait_and_freezes_reconciling_session() -> None:
    """RUNNING 只允许有界等待; RECONCILING session 必须冻结或取消未下发命令。"""

    from src.app.runtime.orchestration.services.device_dispatch_policy import (
        DeviceDispatchDecisionKind,
        DeviceDispatchPolicy,
        DeviceDispatchRequest,
        DeviceRuntimeSnapshot,
        DeviceRuntimeStatus,
    )

    now = timezone.now_for_db()
    policy = DeviceDispatchPolicy()
    running_snapshot = DeviceRuntimeSnapshot(
        device_code="DEV-3",
        status=DeviceRuntimeStatus.RUNNING,
        observed_at=now,
        status_valid_until=now + timedelta(milliseconds=1000),
    )
    wait = policy.evaluate(
        DeviceDispatchRequest(
            command_code="CMD-3",
            device_role="robot",
            capability_code="PUT",
            dispatch_deadline_at=now + timedelta(seconds=1),
        ),
        snapshot=running_snapshot,
        now=now,
    )
    hold = policy.evaluate(
        DeviceDispatchRequest(
            command_code="CMD-3",
            device_role="robot",
            capability_code="PUT",
            dispatch_deadline_at=now,
        ),
        snapshot=running_snapshot,
        now=now + timedelta(milliseconds=1),
    )
    frozen = policy.evaluate(
        DeviceDispatchRequest(
            command_code="CMD-4",
            device_role="robot",
            capability_code="PUT",
            dispatch_deadline_at=now + timedelta(seconds=1),
            session_state="RECONCILING",
        ),
        snapshot=running_snapshot,
        now=now,
    )

    assert wait.kind == DeviceDispatchDecisionKind.WAIT_FOR_IDLE
    assert hold.kind == DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD
    assert hold.runtime_hold_required is True
    assert frozen.kind == DeviceDispatchDecisionKind.FREEZE_OR_CANCEL
    assert frozen.cancel_unsubmitted is True
    assert frozen.freeze_submitted is True


def test_conveyor_queue_writer_resolves_placeholder_and_escalates_conflict() -> None:
    """Queue writer 必须幂等处理 active 唯一和 placeholder resolve。"""

    from src.app.runtime.orchestration.services.conveyor_queue_writer import (
        ConveyorQueueMembershipSnapshot,
        ConveyorQueueWriteDecisionKind,
        ConveyorQueueWriter,
        ConveyorQueueWriteRequest,
    )

    writer = ConveyorQueueWriter()
    placeholder = ConveyorQueueMembershipSnapshot(
        workline_id=1,
        queue_code="Q-IN",
        placeholder_key="scan:001",
    )
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
    conflict = writer.plan_write(
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-OUT",
            bin_code="BIN-002",
            declared_queue_codes=frozenset({"Q-IN", "Q-OUT"}),
        ),
        active_memberships=[ConveyorQueueMembershipSnapshot(workline_id=1, queue_code="Q-IN", bin_code="BIN-002")],
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
    assert conflict.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert conflict.runtime_hold_required is True
    assert blocked.kind == ConveyorQueueWriteDecisionKind.BLOCKED
    assert blocked.reason == "UNKNOWN_QUEUE_CODE"
