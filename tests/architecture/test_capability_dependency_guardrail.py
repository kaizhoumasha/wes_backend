"""Capability dependency guardrails.

CAPABILITY_FORBIDDEN_DEPENDENCY: capability 注入禁用关键词。
包括 HTTP client/service locator/provider exception/DTO。
CAPABILITY_IMPLEMENTATION_IMPORT: capability 不得 import wms_integration/device services/models 实现
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

CAPABILITY_FORBIDDEN_DEPENDENCY_FORBIDDEN = {
    "http_client",
    "service_locator",
    "WmsClientException",
    "DeviceClientException",
}
CAPABILITY_IMPLEMENTATION_IMPORT_PATTERN = "from src.app.wms_integration.services"
CAPABILITY_IMPLEMENTATION_IMPORT_PATTERN2 = "from src.app.device.models"
EXTENSION_PLATFORM_RULE_IDS = {
    "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY",
    "SYSTEM_CAPABILITY_DEPENDENCY_BOUNDARY",
    "RUNTIME_GENERATED_INDEX_STATICITY",
    "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION",
    "LEGACY_CAPABILITY_ROUTING_IMPORT",
}


def test_capability_forbidden_dependency_forbidden_keywords_covered():
    content = GUARDRAIL.read_text()
    assert "rule_capability_forbidden_dependency" in content
    for kw in CAPABILITY_FORBIDDEN_DEPENDENCY_FORBIDDEN:
        assert kw in content


def test_capability_implementation_import_from_import_pattern_covered():
    content = GUARDRAIL.read_text()
    assert "rule_capability_implementation_import" in content
    assert "wms_integration" in content
    assert "device" in content


def test_extension_platform_rule_ids_are_stable_business_names():
    content = GUARDRAIL.read_text(encoding="utf-8")

    assert set(re.findall(r'RULE_[A-Z_]+="([A-Z_]+)"', content)) >= EXTENSION_PLATFORM_RULE_IDS
    assert all("PHASE" not in rule_id and "WAVE" not in rule_id for rule_id in EXTENSION_PLATFORM_RULE_IDS)


def test_capability_forbidden_dependency_violation_fixture():
    """capability 持有 HTTP client 违反 I3。"""
    violation = "http_client = WmsHttpClient()"
    assert any(kw in violation for kw in CAPABILITY_FORBIDDEN_DEPENDENCY_FORBIDDEN)


def test_capability_implementation_import_violation_fixture():
    """capability import wms_integration services 违反 I3。"""
    violation = "from src.app.wms_integration.services.transport_contract import ..."
    assert CAPABILITY_IMPLEMENTATION_IMPORT_PATTERN in violation


def test_capability_implementation_import_allowlist_does_not_use_directory_prefixes():
    """CAPABILITY_IMPLEMENTATION_IMPORT seed allowlist 必须枚举文件，不能用目录前缀吞掉未来违规。"""
    rows = [
        row
        for row in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if row and not row.startswith("#") and row.startswith("CAPABILITY_IMPLEMENTATION_IMPORT|")
    ]

    assert rows
    assert all("|src/app/workline/services/|" not in row for row in rows)
    assert all("|src/app/workline/repositories/|" not in row for row in rows)


def test_capability_implementation_import_directory_prefix_allowlist_is_rejected(tmp_path):
    """必须拒绝 CAPABILITY_IMPLEMENTATION_IMPORT 目录前缀，避免未来违规被同一行吞掉。"""
    current_rows = _allowlist_rows_with_matrix_drop_phase()
    legacy_drop_phase = f"phase{2}"
    current_rows.append(
        "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/workline/services/|"
        "legacy directory prefix must fail|2026-09-30|"
        "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>"
        f"#CAPABILITY_IMPLEMENTATION_IMPORT|{legacy_drop_phase}"
    )

    temp_allowlist = tmp_path / "architecture-guardrails.allowlist"
    temp_allowlist.write_text("\n".join(current_rows) + "\n", encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced", "--allowlist", str(temp_allowlist)],
        cwd=REPO_ROOT,
        env={**os.environ, "ARCHITECTURE_GUARDRAILS_VALIDATE_ONLY": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "CAPABILITY_IMPLEMENTATION_IMPORT 必须逐文件枚举" in result.stderr


def test_new_extension_platform_path_allowlist_is_rejected(tmp_path):
    """新平台作者态目录不允许用临时迁移豁免掩盖违规。"""
    rows = _allowlist_rows_with_matrix_drop_phase()
    rows.append(
        "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY|src/app/runtime/workline_plugins/rough_sorter/handlers.py|"
        "must fail|2026-08-15|legacy:test:must-fail|task10"
    )

    result = _run_guardrail_with_allowlist(tmp_path, rows)

    assert result.returncode == 1
    assert "新扩展平台目录禁止 allowlist" in result.stderr


def _active_allowlist_rows() -> list[str]:
    return [row for row in ALLOWLIST.read_text(encoding="utf-8").splitlines() if row and not row.startswith("#")]


def _matrix_drop_phases() -> dict[str, str]:
    with open(REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv", newline="", encoding="utf-8") as f:
        return {row["entry_id"]: row["drop_phase"] for row in csv.DictReader(f)}


def _allowlist_rows_with_matrix_drop_phase() -> list[str]:
    drop_phases = _matrix_drop_phases()
    rows = []
    for row in _active_allowlist_rows():
        parts = row.split("|")
        if len(parts) == 5:
            parts.append(drop_phases[parts[4]])
        rows.append("|".join(parts))
    return rows


def _run_guardrail_with_allowlist(
    tmp_path: Path, rows: list[str], mode: str = "enforced"
) -> subprocess.CompletedProcess:
    temp_allowlist = tmp_path / "architecture-guardrails.allowlist"
    temp_allowlist.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", mode, "--allowlist", str(temp_allowlist)],
        cwd=REPO_ROOT,
        env={**os.environ, "ARCHITECTURE_GUARDRAILS_VALIDATE_ONLY": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_allowlist_rows_declare_matrix_drop_phase():
    """seed allowlist 必须显式声明 drop_phase，供脚本与 matrix 机器比对。"""
    rows = _active_allowlist_rows()

    assert rows
    assert all(len(row.split("|")) == 6 for row in rows)


def test_guardrail_resolves_python_interpreter_for_csv_parser():
    """guardrail 由 bash 直接运行，CSV helper 不能依赖裸 python alias。"""
    content = GUARDRAIL.read_text(encoding="utf-8")

    assert "run_python()" in content
    assert 'run_python - "$legacy_entry_id"' in content
    assert not re.search(r'^\s*python\s+-\s+"\$legacy_entry_id"', content, re.MULTILINE)


def test_allowlist_rejects_short_legacy_entry_id(tmp_path):
    """legacy_entry_id 必须精确匹配 CSV 第一列，不能靠子串误绿。"""
    rows = _allowlist_rows_with_matrix_drop_phase()
    bad_entry = "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>"
    rows = [
        row.replace(
            "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>#CAPABILITY_IMPLEMENTATION_IMPORT",
            bad_entry,
        )
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows)

    assert result.returncode == 1
    assert "legacy_entry_id 精确匹配失败" in result.stderr


def test_allowlist_rejects_drop_phase_mismatch(tmp_path):
    """allowlist 声明的 drop_phase 必须与 legacy matrix 一致。"""
    rows = _allowlist_rows_with_matrix_drop_phase()
    device_gateway_entry = (
        "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>"
        "#CAPABILITY_IMPLEMENTATION_IMPORT|"
    )
    old_drop_phase = f"phase{2}"
    mismatched_drop_phase = f"phase{4}"
    rows = [
        row.replace(
            f"{device_gateway_entry}{old_drop_phase}",
            f"{device_gateway_entry}{mismatched_drop_phase}",
        )
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows)

    assert result.returncode == 1
    assert "drop_phase 不一致" in result.stderr


def test_allowlist_rejects_invalid_expires_at(tmp_path):
    rows = _allowlist_rows_with_matrix_drop_phase()
    rows = [
        row.replace(
            "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, 运行态服务从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-09-30|",
            "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, 运行态服务从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|not-a-date|",
        )
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows)

    assert result.returncode == 1
    assert "expires_at 日期无效" in result.stderr


def test_allowlist_rejects_invalid_calendar_expires_at(tmp_path):
    rows = _allowlist_rows_with_matrix_drop_phase()
    rows = [
        row.replace(
            "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, 运行态服务从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-09-30|",
            "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, 运行态服务从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-02-31|",
        )
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows)

    assert result.returncode == 1
    assert "expires_at 日期无效" in result.stderr


def test_expiry_check_rejects_expired_allowlist_rows(tmp_path):
    rows = _allowlist_rows_with_matrix_drop_phase()
    rows = [
        row.replace(
            "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, 运行态服务从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-09-30|",
            "CAPABILITY_IMPLEMENTATION_IMPORT|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, 运行态服务从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2000-01-01|",
        )
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows, mode="expiry-check")

    assert result.returncode == 1
    assert "allowlist 已过期" in result.stderr
