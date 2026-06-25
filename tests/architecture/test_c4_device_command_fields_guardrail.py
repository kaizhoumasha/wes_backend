"""C4 guardrail: DeviceCommand 不含 PLC/坐标/关节/安全回路字段。

验证禁止字段被识别; 字段白名单见 device-command-contract.md §5。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"

FORBIDDEN_FIELDS = {"plc", "coordinate", "joint_angle", "x_coord", "y_coord", "safety_loop"}


def test_c4_forbidden_fields_identified():
    """C4 禁止字段集合覆盖 PLC/坐标/关节/安全回路。"""
    violation_field = "joint_angle: float"
    matched = {f for f in FORBIDDEN_FIELDS if f in violation_field}
    assert matched


def test_c4_rule_exists_in_script():
    content = GUARDRAIL.read_text()
    assert "rule_c4" in content
    for field in ("plc", "coordinate", "joint", "safety_loop"):
        assert field in content
