"""BC-02 Runtime Snapshot 行为契约（strict xfail）。

验收: active session 可查询 state、timeline、inbox、hold、pending intent、correlation。
Phase 0 缺 Phase 1 ExecutionSession/RuntimeInbox/RuntimeIntentLog schema,
用 strict xfail 标明解除条件。Phase 1 CEO-007 完成后解除。
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="Phase 1 CEO-007 ExecutionSession/RuntimeInbox schema 未实现")
def test_runtime_snapshot_exposes_state_timeline_inbox_hold_intent_correlation():
    """目标态: snapshot 必须含 state/timeline/inbox/hold/pending intent/correlation。

    Phase 0 占位断言; Phase 1 接入真实 ExecutionSession 后补全字段断言。
    """
    # 占位: Phase 1 实现后替换为真实 snapshot assembler 断言
    snapshot = {}  # type: ignore[var-annotated]
    required_fields = {"state", "timeline", "inbox", "hold", "pending_intent", "correlation"}
    assert required_fields.issubset(snapshot.keys())
