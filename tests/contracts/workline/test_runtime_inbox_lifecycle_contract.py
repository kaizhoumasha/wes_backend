"""BC-XX RuntimeInbox lifecycle 行为契约。

验收: RuntimeInbox claim/process/retry/dead-letter 5 态状态机正确推进;
       max_retries 耗尽进入 DEAD_LETTER; lease_until 过期允许 crash replay。
mock 仅允许 `src/app/runtime/orchestration/` 内的 skeleton 实体。
"""

from __future__ import annotations

import pytest

from tests.support.runtime_inbox_contract import (
    RuntimeInboxEntry,
    can_retry,
    is_terminal,
    transition,
)


def test_received_to_processing_to_processed_happy_path():
    """happy path: RECEIVED → PROCESSING → PROCESSED。"""
    entry = RuntimeInboxEntry(status="RECEIVED", attempt_count=0, max_retries=5)

    advanced = transition(entry, to_status="PROCESSING", now=0.0)
    assert advanced.status == "PROCESSING"

    advanced = transition(advanced, to_status="PROCESSED", now=1.0)
    assert advanced.status == "PROCESSED"
    assert is_terminal(advanced)


def test_failed_retry_recovers_when_attempts_below_max():
    """retry path: PROCESSING → FAILED 后 attempt_count 仍低于 max_retries。"""
    entry = RuntimeInboxEntry(
        status="PROCESSING",
        attempt_count=1,
        max_retries=5,
        lease_until=10.0,
    )

    advanced = transition(entry, to_status="FAILED", now=20.0)
    assert advanced.status == "FAILED"
    assert advanced.attempt_count == 2
    assert advanced.next_retry_at is not None
    assert can_retry(advanced, now=advanced.next_retry_at)


def test_failed_transitions_to_dead_letter_when_max_retries_exceeded():
    """dead-letter path: 超过 max_retries 直接进入 DEAD_LETTER。"""
    entry = RuntimeInboxEntry(
        status="PROCESSING",
        attempt_count=5,
        max_retries=5,
        lease_until=10.0,
    )

    advanced = transition(entry, to_status="FAILED", now=20.0)
    assert advanced.status == "DEAD_LETTER"
    assert is_terminal(advanced)
    assert not can_retry(advanced, now=20.0)


def test_dead_letter_is_terminal_and_blocks_retry():
    """error path: DEAD_LETTER 不可再推进。"""
    entry = RuntimeInboxEntry(status="DEAD_LETTER", attempt_count=5, max_retries=5)

    assert is_terminal(entry)
    assert not can_retry(entry, now=20.0)


def test_processing_can_crash_replay_back_to_received_when_lease_expired():
    """crash replay: lease_until 过期允许 PROCESSING → RECEIVED 重派。"""
    entry = RuntimeInboxEntry(
        status="PROCESSING",
        attempt_count=1,
        max_retries=5,
        lease_until=100.0,
    )

    advanced = transition(entry, to_status="RECEIVED", now=200.0)
    assert advanced.status == "RECEIVED"
    assert advanced.attempt_count == 1


def test_illegal_transition_raises_value_error():
    """error path: 非法转移 (RECEIVED -> PROCESSED) 直接拒绝。"""
    entry = RuntimeInboxEntry(status="RECEIVED", attempt_count=0, max_retries=5)

    with pytest.raises(ValueError, match="非法转移"):
        transition(entry, to_status="PROCESSED", now=0.0)


def test_lease_not_expired_blocks_crash_replay_to_received():
    """error path: lease 未过期不允许 PROCESSING → RECEIVED 重派。"""
    entry = RuntimeInboxEntry(
        status="PROCESSING",
        attempt_count=1,
        max_retries=5,
        lease_until=100.0,
    )

    with pytest.raises(ValueError, match="lease 过期"):
        transition(entry, to_status="RECEIVED", now=50.0)
