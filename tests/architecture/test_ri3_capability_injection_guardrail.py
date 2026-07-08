"""R-I3a/R-I3b guardrail: capability 注入只能暴露 port contract。

R-I3a: capability 注入禁用关键词 (HTTP client/service locator/provider exception/DTO)
R-I3b: capability 不得 import wms_integration/device services/models 实现
"""

from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

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


def test_ri3b_allowlist_does_not_use_directory_prefixes():
    """R-I3b seed allowlist 必须枚举文件，不能用目录前缀吞掉未来违规。"""
    rows = [
        row
        for row in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if row and not row.startswith("#") and row.startswith("R-I3b|")
    ]

    assert rows
    assert all("|src/app/workline/services/|" not in row for row in rows)
    assert all("|src/app/workline/repositories/|" not in row for row in rows)


def test_ri3b_directory_prefix_allowlist_is_rejected(tmp_path):
    """必须拒绝 R-I3b 目录前缀，避免未来违规被同一行吞掉。"""
    current_rows = _allowlist_rows_with_matrix_drop_phase()
    current_rows.append(
        "R-I3b|src/app/workline/services/|legacy directory prefix must fail|2026-09-30|"
        "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>#R-I3b|phase" + "2"
    )

    temp_allowlist = tmp_path / "architecture-guardrails.allowlist"
    temp_allowlist.write_text("\n".join(current_rows) + "\n", encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced", "--allowlist", str(temp_allowlist)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "R-I3b 必须逐文件枚举" in result.stderr


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
        row.replace("legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>#R-I3b", bad_entry)
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows)

    assert result.returncode == 1
    assert "legacy_entry_id 精确匹配失败" in result.stderr


def test_allowlist_rejects_drop_phase_mismatch(tmp_path):
    """allowlist 声明的 drop_phase 必须与 legacy matrix 一致。"""
    rows = _allowlist_rows_with_matrix_drop_phase()
    rows = [
        row.replace(
            "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>#R-I3b|phase" + "2",
            "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>#R-I3b|phase" + "4",
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
            "R-I3b|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, runtime migration 从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-09-30|",
            "R-I3b|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, runtime migration 从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|not-a-date|",
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
            "R-I3b|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, runtime migration 从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-09-30|",
            "R-I3b|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, runtime migration 从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-02-31|",
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
            "R-I3b|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, runtime migration 从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2026-09-30|",
            "R-I3b|src/app/runtime/orchestration/services/device_command_gateway.py|"
            "legacy capability import device 实现, runtime migration 从 workline/services/device_command_gateway.py 迁入 runtime/orchestration/services/|2000-01-01|",
        )
        for row in rows
    ]

    result = _run_guardrail_with_allowlist(tmp_path, rows, mode="expiry-check")

    assert result.returncode == 1
    assert "allowlist 已过期" in result.stderr
