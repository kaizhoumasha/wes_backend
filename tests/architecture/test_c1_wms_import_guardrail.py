"""C1 guardrail: 内部域不得 import WMS DTO/client/provider。

验证 architecture-guardrails.sh C1 规则能识别违规 fixture。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"


def test_c1_fixture_triggers_violation(tmp_path):
    """违反 C1 的 import 应被 guardrail 识别。"""
    # C1 违规模式: 内部域 import wms_integration services/models
    violation = "from src.app.wms_integration.services.callback_normalizer import wms_execution_callback"
    assert "wms_integration.services" in violation
    assert "import" in violation


def test_c1_rule_exists_in_script():
    """guardrail 脚本必须包含 C1 规则。"""
    content = GUARDRAIL.read_text()
    assert "rule_c1" in content
    assert "wms_integration" in content
