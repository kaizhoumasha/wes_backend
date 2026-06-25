"""BC-08 / BC-10 External Event 行为契约。

BC-08: 缺 event_id 的离散事件可以 ACK, 但不得推进 session 归属。必须通过。
BC-10: Event_Push HTTP 响应只 ACK, 拦截 command-like 字段。必须通过。
"""

from __future__ import annotations

from tests.support.workline_contracts import (
    event_can_advance_correlation,
    validate_event_push_response,
)

# BC-08


def test_event_with_event_id_advances_correlation():
    ok, reason = event_can_advance_correlation({"data": {"event_id": "evt-001"}})
    assert ok
    assert reason is None


def test_event_without_event_id_acked_but_not_correlated():
    """缺 event_id: ACK 成功, 但不推进 correlation (函数返回 False)。"""
    ok, reason = event_can_advance_correlation({"data": {}})
    assert not ok
    assert reason == "MISSING_EVENT_ID"


def test_event_with_empty_event_id_not_correlated():
    ok, reason = event_can_advance_correlation({"data": {"event_id": ""}})
    assert not ok
    assert reason == "MISSING_EVENT_ID"


# BC-10


def test_event_push_response_ack_only_accepted():
    ok, reason = validate_event_push_response({"status": "ACK"})
    assert ok
    assert reason is None


def test_event_push_response_rejects_command_like_field():
    ok, reason = validate_event_push_response({"status": "ACK", "action": "MOVE_BIN"})
    assert not ok
    assert "COMMAND_LIKE_FIELD" in (reason or "")


def test_event_push_response_rejects_next_action_field():
    ok, reason = validate_event_push_response({"next_action": "PICK"})
    assert not ok
    assert reason == "COMMAND_LIKE_FIELD_next_action"


def test_event_push_response_rejects_non_ack_status():
    ok, reason = validate_event_push_response({"status": "MOVE"})
    assert not ok
    assert reason == "NON_ACK_STATUS"
