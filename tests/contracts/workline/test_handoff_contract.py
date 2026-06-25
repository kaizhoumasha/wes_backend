"""BC-03 Handoff 行为契约。

验收: 交接只能由 callback 或 RuntimeIntentLog evidence 推进;
无 evidence 时 HOLD/拒绝, 禁止 API 层直接改投影。必须通过; 不可 skip。
"""

from __future__ import annotations

from tests.support.workline_contracts import HandoffEvidence, handoff_can_advance


def test_handoff_advances_with_callback_evidence():
    ok, reason = handoff_can_advance(HandoffEvidence(has_callback=True))
    assert ok
    assert reason is None


def test_handoff_advances_with_intent_evidence():
    ok, reason = handoff_can_advance(HandoffEvidence(has_intent_evidence=True))
    assert ok
    assert reason is None


def test_handoff_rejected_without_evidence():
    ok, reason = handoff_can_advance(HandoffEvidence())
    assert not ok
    assert reason == "NO_EVIDENCE"
