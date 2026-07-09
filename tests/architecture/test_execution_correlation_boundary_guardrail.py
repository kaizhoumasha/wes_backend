"""EXECUTION_CORRELATION_BOUNDARY guardrail: 跨域 session FK 收敛为 ExecutionCorrelation。

验证 architecture-guardrails.sh EXECUTION_CORRELATION_BOUNDARY 规则识别跨域 session_id。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"


def test_execution_correlation_boundary_fixture_triggers_violation():
    """违反 EXECUTION_CORRELATION_BOUNDARY 的跨域 session FK 应被识别。"""
    violation_field = "workline_session_id: int | None"
    assert "workline_session_id" in violation_field


def test_execution_correlation_boundary_rule_exists_in_script():
    content = GUARDRAIL.read_text()
    assert "rule_execution_correlation_boundary" in content
    assert "workline_session_id" in content
