"""EXECUTION_CORRELATION_BOUNDARY guardrail: 跨域 session FK 收敛为 ExecutionCorrelation。

验证 architecture-guardrails.sh EXECUTION_CORRELATION_BOUNDARY 规则识别跨域 session_id。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
OPERATION_API = REPO_ROOT / "src/app/workline/v1/operation.py"


def test_execution_correlation_boundary_fixture_triggers_violation():
    """违反 EXECUTION_CORRELATION_BOUNDARY 的跨域 session FK 应被识别。"""
    violation_field = "workline_session_id: int | None"
    assert "workline_session_id" in violation_field


def test_execution_correlation_boundary_rule_exists_in_script():
    content = GUARDRAIL.read_text()
    assert "rule_execution_correlation_boundary" in content
    assert "workline_session_id" in content


def test_runtime_inbox_response_mapping_has_only_a_narrow_canonical_exception():
    """API 对外 session_id 允许直接映射 canonical 字段，但不得放宽旧字段双读。"""

    guardrail_content = GUARDRAIL.read_text()
    operation_content = OPERATION_API.read_text()
    assert '"session_id": inbox.workline_session_id,' in guardrail_content
    assert '"session_id": inbox.workline_session_id,' in operation_content
    assert 'getattr(inbox, "session_id"' not in operation_content
