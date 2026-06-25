"""BC-04 Resource Projection 行为契约。

验收: 同一 object 在同一 WorkLine 内只能有一个可解释 active 归属;
瞬态冲突必须带 transient_until, 超时进入 RECONCILING。必须通过; 不可 skip。
"""

from __future__ import annotations

from tests.support.workline_contracts import ActiveOwnership, assert_single_active_ownership


def test_first_active_owner_accepted():
    ok, _ = assert_single_active_ownership(
        existing=None,
        new_claim=ActiveOwnership(object_key="BIN-001", workline_id="WL-1"),
        now=100.0,
    )
    assert ok


def test_second_active_owner_same_object_rejected():
    existing = ActiveOwnership(object_key="BIN-001", workline_id="WL-1")
    ok, reason = assert_single_active_ownership(
        existing=existing,
        new_claim=ActiveOwnership(object_key="BIN-001", workline_id="WL-1"),
        now=200.0,
    )
    assert not ok
    assert reason == "DUPLICATE_ACTIVE_OWNER"


def test_transient_window_allows_temporary_duplicate():
    existing = ActiveOwnership(object_key="BIN-001", workline_id="WL-1", transient_until=150.0)
    # 窗口内合法
    ok, reason = assert_single_active_ownership(
        existing=existing,
        new_claim=ActiveOwnership(object_key="BIN-001", workline_id="WL-1"),
        now=100.0,
    )
    assert ok
    assert reason == "TRANSIENT_WINDOW"
    # 超时进 RECONCILING
    ok, reason = assert_single_active_ownership(
        existing=existing,
        new_claim=ActiveOwnership(object_key="BIN-001", workline_id="WL-1"),
        now=200.0,
    )
    assert not ok
    assert reason == "DUPLICATE_ACTIVE_OWNER"


def test_different_object_does_not_conflict():
    existing = ActiveOwnership(object_key="BIN-001", workline_id="WL-1")
    ok, _ = assert_single_active_ownership(
        existing=existing,
        new_claim=ActiveOwnership(object_key="BIN-002", workline_id="WL-1"),
        now=200.0,
    )
    assert ok


def test_different_workline_does_not_conflict():
    existing = ActiveOwnership(object_key="BIN-001", workline_id="WL-1")
    ok, _ = assert_single_active_ownership(
        existing=existing,
        new_claim=ActiveOwnership(object_key="BIN-001", workline_id="WL-2"),
        now=200.0,
    )
    assert ok
