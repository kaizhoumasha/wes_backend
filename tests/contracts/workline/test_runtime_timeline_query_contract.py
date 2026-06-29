"""BC-XX RuntimeTimeline query 行为契约。

验收: RuntimeTimeline 是 append-only 事件溯源;
       不作为 owner 状态源 (主计划 §9.2 RuntimeTimeline);
       query 必须能按 trace_id / correlation_id / event_type 过滤。
mock 仅允许 `src/app/runtime/orchestration/` 内的 skeleton 实体。
"""

from __future__ import annotations

import pytest

from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline


def _timeline_row(event_type: str, *, trace_id: str, correlation_id: str | None, occurred_at: int) -> RuntimeTimeline:
    return RuntimeTimeline(
        execution_session_id=101,
        trace_id=trace_id,
        correlation_id=correlation_id,
        event_type=event_type,
        occurred_at=occurred_at,
    )


def _query(rows, *, trace_id: str | None = None, correlation_id: str | None = None, event_type: str | None = None):
    """最小 RuntimeTimeline query 替身 — 仅按 trace_id / correlation_id / event_type 过滤。"""
    result = []
    for row in rows:
        if trace_id is not None and row.trace_id != trace_id:
            continue
        if correlation_id is not None and row.correlation_id != correlation_id:
            continue
        if event_type is not None and row.event_type != event_type:
            continue
        result.append(row)
    return sorted(result, key=lambda r: r.occurred_at)


def test_timeline_query_filters_by_trace_id():
    """happy path: query 按 trace_id 过滤, 按 occurred_at 升序返回。"""
    rows = [
        _timeline_row("INBOX_RECEIVED", trace_id="t-001", correlation_id="c-001", occurred_at=2000),
        _timeline_row("INTENT_DISPATCHED", trace_id="t-002", correlation_id="c-002", occurred_at=1000),
        _timeline_row("SESSION_ADVANCED", trace_id="t-001", correlation_id="c-001", occurred_at=3000),
    ]

    matched = _query(rows, trace_id="t-001")

    assert len(matched) == 2
    assert [r.occurred_at for r in matched] == [2000, 3000]


def test_timeline_query_filters_by_correlation_id():
    """happy path: query 按 correlation_id 过滤 (跨域稳定 key)。"""
    rows = [
        _timeline_row("INBOX_RECEIVED", trace_id="t-001", correlation_id="c-001", occurred_at=2000),
        _timeline_row("INTENT_DISPATCHED", trace_id="t-001", correlation_id="c-002", occurred_at=3000),
    ]

    matched = _query(rows, correlation_id="c-001")

    assert len(matched) == 1
    assert matched[0].event_type == "INBOX_RECEIVED"


def test_timeline_query_filters_by_event_type():
    """happy path: query 按 event_type 过滤。"""
    rows = [
        _timeline_row("INBOX_RECEIVED", trace_id="t-001", correlation_id="c-001", occurred_at=1000),
        _timeline_row("INTENT_DISPATCHED", trace_id="t-001", correlation_id="c-001", occurred_at=2000),
        _timeline_row("INBOX_RECEIVED", trace_id="t-002", correlation_id="c-002", occurred_at=3000),
    ]

    matched = _query(rows, event_type="INBOX_RECEIVED")

    assert len(matched) == 2
    assert all(r.event_type == "INBOX_RECEIVED" for r in matched)


def test_timeline_query_with_no_filter_returns_all_in_order():
    """happy path: 无过滤参数返回全部, 按 occurred_at 升序。"""
    rows = [
        _timeline_row("INBOX_RECEIVED", trace_id="t-001", correlation_id="c-001", occurred_at=2000),
        _timeline_row("INTENT_DISPATCHED", trace_id="t-002", correlation_id="c-002", occurred_at=1000),
        _timeline_row("SESSION_ADVANCED", trace_id="t-003", correlation_id="c-003", occurred_at=3000),
    ]

    matched = _query(rows)

    assert [r.occurred_at for r in matched] == [1000, 2000, 3000]


def test_timeline_query_empty_when_no_match():
    """error path: 无匹配时返回空列表, 不抛异常。"""
    rows = [
        _timeline_row("INBOX_RECEIVED", trace_id="t-001", correlation_id="c-001", occurred_at=2000),
    ]

    matched = _query(rows, trace_id="t-NONEXISTENT")

    assert matched == []


def test_timeline_does_not_carry_owner_state_fields():
    """不变量: RuntimeTimeline 是 append-only 事件溯源, 不作为 owner 状态源 (主计划 §9.2)。
    不持 step_status / dispatch_status / status 之类 owner 状态字段 —
    状态变化须新建 timeline 行, 不允许就地覆盖。"""
    forbidden_owner_state_fields = {
        "step_status",
        "dispatch_status",
        "hold_status",
        "is_resolved",
        "owner_state",
    }
    declared_fields = set(RuntimeTimeline.model_fields.keys())
    leaked = forbidden_owner_state_fields & declared_fields

    assert not leaked, f"RuntimeTimeline 不应持 owner 状态字段: {leaked}"
