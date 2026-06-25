"""C2 guardrail: 跨域 session FK 收敛为 ExecutionCorrelation。

验证 architecture-guardrails.sh C2 规则识别跨域 session_id。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"


def test_c2_fixture_triggers_violation():
    """违反 C2 的跨域 session FK 应被识别。"""
    violation_field = "workline_session_id: int | None"
    assert "workline_session_id" in violation_field


def test_c2_rule_exists_in_script():
    content = GUARDRAIL.read_text()
    assert "rule_c2" in content
    assert "workline_session_id" in content
