"""R-I3a/R-I3b guardrail: capability 注入只能暴露 port contract。

R-I3a: capability 注入禁用关键词 (HTTP client/service locator/provider exception/DTO)
R-I3b: capability 不得 import wms_integration/device services/models 实现
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"

RI3A_FORBIDDEN = {"http_client", "service_locator", "WmsClientException", "DeviceClientException"}
RI3B_PATTERN = "from src.app.wms_integration.services"
RI3B_PATTERN2 = "from src.app.device.models"


def test_ri3a_forbidden_keywords_covered():
    content = GUARDRAIL.read_text()
    assert "rule_ri3a" in content
    for kw in RI3A_FORBIDDEN:
        assert kw in content


def test_ri3b_from_import_pattern_covered():
    content = GUARDRAIL.read_text()
    assert "rule_ri3b" in content
    assert "wms_integration" in content
    assert "device" in content


def test_ri3a_violation_fixture():
    """capability 持有 HTTP client 违反 I3。"""
    violation = "http_client = WmsHttpClient()"
    assert any(kw in violation for kw in RI3A_FORBIDDEN)


def test_ri3b_violation_fixture():
    """capability import wms_integration services 违反 I3。"""
    violation = "from src.app.wms_integration.services.transport_contract import ..."
    assert RI3B_PATTERN in violation
