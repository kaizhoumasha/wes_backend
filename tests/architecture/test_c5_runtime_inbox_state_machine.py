"""C5 RuntimeInbox 状态机契约测试。

覆盖主计划 §9.2 + SPEC P0-007 C5 的 6 种状态转移。
使用 tests/support/runtime_inbox_contract.py 目标态模型, 不 import legacy WorklineInbox。
"""

from __future__ import annotations

import pytest

from tests.support.runtime_inbox_contract import (
    LEGAL_TRANSITIONS,
    RuntimeInboxEntry,
    can_retry,
    is_terminal,
    transition,
)


def test_received_to_processing():
    entry = transition(RuntimeInboxEntry(), "PROCESSING", now=100.0)
    assert entry.status == "PROCESSING"
    assert entry.lease_until == 130.0


def test_processing_to_processed():
    entry = RuntimeInboxEntry(status="PROCESSING")
    entry = transition(entry, "PROCESSED", now=100.0)
    assert entry.status == "PROCESSED"
    assert is_terminal(entry)


def test_processing_to_failed_records_retry():
    entry = RuntimeInboxEntry(status="PROCESSING")
    entry = transition(entry, "FAILED", now=100.0)
    assert entry.status == "FAILED"
    assert entry.attempt_count == 1
    assert entry.next_retry_at == 102.0  # 2s 退避


def test_failed_to_received_on_retry_time():
    entry = RuntimeInboxEntry(status="FAILED", attempt_count=1, next_retry_at=102.0, max_retries=5)
    assert not can_retry(entry, now=101.0)
    assert can_retry(entry, now=102.0)
    entry = transition(entry, "RECEIVED", now=102.0)
    assert entry.status == "RECEIVED"


def test_failed_to_dead_letter_on_max_retries():
    entry = RuntimeInboxEntry(status="PROCESSING", attempt_count=5, max_retries=5)
    entry = transition(entry, "FAILED", now=100.0)
    assert entry.status == "DEAD_LETTER"
    assert is_terminal(entry)
    assert entry.next_retry_at is None


def test_processing_to_received_on_lease_expiry():
    """lease_until 过期, 允许 crash replay 回 RECEIVED。"""
    entry = RuntimeInboxEntry(status="PROCESSING", lease_until=130.0)
    entry = transition(entry, "RECEIVED", now=140.0)
    assert entry.status == "RECEIVED"


def test_processing_to_received_rejects_active_lease():
    """lease_until 尚未过期, 禁止 crash replay 回 RECEIVED。"""
    entry = RuntimeInboxEntry(status="PROCESSING", lease_until=130.0)
    with pytest.raises(ValueError, match="lease"):
        transition(entry, "RECEIVED", now=120.0)


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("RECEIVED", "PROCESSED"),
        ("RECEIVED", "DEAD_LETTER"),
        ("PROCESSED", "RECEIVED"),
        ("DEAD_LETTER", "PROCESSING"),
        ("FAILED", "PROCESSING"),
    ],
)
def test_illegal_transitions_rejected(from_status, to_status):
    entry = RuntimeInboxEntry(status=from_status)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="非法转移"):
        transition(entry, to_status, now=100.0)  # type: ignore[arg-type]


def test_dead_letter_is_terminal():
    assert "DEAD_LETTER" in LEGAL_TRANSITIONS
    assert LEGAL_TRANSITIONS["DEAD_LETTER"] == set()
