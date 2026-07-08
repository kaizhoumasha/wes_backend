"""WMS_INTEGRATION_BOUNDARY guardrail: 内部域不得 import WMS DTO/client/provider。

验证 architecture-guardrails.sh WMS_INTEGRATION_BOUNDARY 规则能识别违规 fixture。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"


def test_wms_integration_boundary_fixture_triggers_violation(tmp_path):
    """违反 WMS_INTEGRATION_BOUNDARY 的 import 应被 guardrail 识别。"""
    # WMS_INTEGRATION_BOUNDARY 违规模式: 内部域 import wms_integration services/models
    violation = "from src.app.wms_integration.services.callback_normalizer import wms_execution_callback"
    assert "wms_integration.services" in violation
    assert "import" in violation


def test_wms_integration_boundary_rule_exists_in_script():
    """guardrail 脚本必须包含 WMS_INTEGRATION_BOUNDARY 规则。"""
    content = GUARDRAIL.read_text()
    assert "rule_wms_integration_boundary" in content
    assert "wms_integration" in content
