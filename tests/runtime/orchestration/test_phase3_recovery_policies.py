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
