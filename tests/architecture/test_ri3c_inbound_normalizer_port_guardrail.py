"""R-I3c inbound normalizer port 静态扫描器测试 (Phase 1 CEO-009 / Packet D)。

主计划 §3.5.1 + H2: 业务 capability (src/app/runtime + src/app/workline) 不得持有
inbound normalizer Protocol (WmsEventPort / DeviceEventPort / InboundEventPort) 或
RuntimeInbox / RuntimeInboxConsumer; 这些是 RuntimeInboxConsumer 专属依赖。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_SCRIPT = REPO_ROOT / "scripts" / "architecture-guardrails.sh"

# 与 scripts/architecture-guardrails.sh rule_ri3c 完全一致的扫描模式
RI3C_TYPE_NAMES = ("WmsEventPort", "DeviceEventPort", "InboundEventPort", "RuntimeInbox", "RuntimeInboxConsumer")
RI3C_SCAN_SCOPE = ("src/app/runtime", "src/app/workline")
RI3C_EXCLUDED_PATHS = (
    "src/app/wms_integration/ports/event.py",
    "src/app/wms_integration/ports/__init__.py",
    "src/app/runtime/capability_port_registry.py",
    "src/app/runtime/inbound_normalizer_registry.py",
    "src/app/contracts/external_contract_profile.py",
    "src/app/runtime/orchestration/consumers/",
)


def test_ri3c_rule_registered_in_guardrails_script():
    """architecture-guardrails.sh 包含 rule_ri3c 函数并加入调用链。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    assert "rule_ri3c" in text
    # 必须在主调用链里被调用
    assert re.search(r"^\s*rule_ri3c\s*$", text, flags=re.MULTILINE), "rule_ri3c 未在主调用链调用"


def test_ri3c_rule_scans_correct_paths():
    """rule_ri3c 必须扫描 src/app/runtime + src/app/workline (与 R-I3a/R-I3b 一致)。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    # 提取 rule_ri3c 函数体
    m = re.search(r"rule_ri3c\(\)\s*\{(.*?)^\}", text, flags=re.DOTALL | re.MULTILINE)
    assert m, "rule_ri3c 函数未找到"
    body = m.group(1)
    for scope in RI3C_SCAN_SCOPE:
        assert scope in body, f"rule_ri3c 缺扫描路径 {scope}"


def test_ri3c_rule_excludes_legitimate_holders():
    """rule_ri3c 必须排除合法的 inbound normalizer 持有者。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"rule_ri3c\(\)\s*\{(.*?)^\}", text, flags=re.DOTALL | re.MULTILINE)
    assert m
    body = m.group(1)
    for excluded in RI3C_EXCLUDED_PATHS:
        assert excluded in body, f"rule_ri3c 缺排除路径 {excluded}"


def test_ri3c_rule_pattern_covers_all_inbound_normalizer_types():
    """rule_ri3c 匹配 5 个 inbound normalizer 类型名 (WmsEventPort 等)。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"rule_ri3c\(\)\s*\{(.*?)^\}", text, flags=re.DOTALL | re.MULTILINE)
    assert m
    body = m.group(1)
    # 找第一个 local pattern='...' 赋值
    pm = re.search(r"local\s+pattern='([^']+)'", body)
    assert pm, "rule_ri3c 缺 local pattern"
    pattern = pm.group(1)
    for name in RI3C_TYPE_NAMES:
        assert name in pattern, f"rule_ri3c pattern 缺类型 {name}"


def test_ri3c_guardrail_runs_clean_in_phase1():
    """phase1 模式运行 architecture-guardrails.sh, 当前代码无 R-I3c 违规 (退出码 0)。"""
    # NOTE: brief had `sys.executable` here, but architecture-guardrails.sh has
    # `#!/usr/bin/env bash` shebang and is not a Python script. Use bash.
    result = subprocess.run(
        ["bash", str(GUARDRAILS_SCRIPT), "--phase", "phase1"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # 当前所有 inbound normalizer 类型持有者都在 allowlist 之外但命中 exclusion
    # 或不存在 capability 路径 import, 应返回 0
    assert result.returncode == 0, (
        f"architecture-guardrails.sh phase1 exit={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_import_linter_config_exists():
    """.import-linter.ini 存在且包含 capability-isolation contract。"""
    ini = REPO_ROOT / ".import-linter.ini"
    assert ini.exists(), ".import-linter.ini 不存在"
    text = ini.read_text(encoding="utf-8")
    assert "[importlinter:contract:capability-isolation]" in text
    assert "type = forbidden" in text
    assert "forbidden_modules" in text


def test_import_linter_check_script_runs_clean():
    """scripts/import-linter-check.sh 当前 capability-isolation contract 0 违规。"""
    script = REPO_ROOT / "scripts" / "import-linter-check.sh"
    assert script.exists(), "scripts/import-linter-check.sh 不存在"
    result = subprocess.run(
        ["bash", str(script)],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # 当前 capability_port_registry 不 import 任何 wms_integration 子模块, 应 0 违规
    assert result.returncode == 0, (
        f"import-linter-check.sh exit={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
