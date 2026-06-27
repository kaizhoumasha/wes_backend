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
    "src/app/runtime/orchestration/__init__.py",
    "src/app/runtime/orchestration/runtime_inbox.py",
    "src/app/runtime/orchestration/consumers/*",
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
    assert "src/app/runtime/orchestration/*" not in body, "R-I3c 禁止排除整个 orchestration 目录"
    for excluded in RI3C_EXCLUDED_PATHS:
        assert excluded in body, f"rule_ri3c 缺排除路径 {excluded}"


def test_ri3c_rule_pattern_covers_all_inbound_normalizer_types():
    """rule_ri3c 匹配 5 个 inbound normalizer 类型名 (WmsEventPort 等)。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"rule_ri3c\(\)\s*\{(.*?)^\}", text, flags=re.DOTALL | re.MULTILINE)
    assert m
    body = m.group(1)
    for name in RI3C_TYPE_NAMES:
        assert name in body, f"rule_ri3c pattern 缺类型 {name}"
    assert "from .* import" in body
    assert "^[[:space:]]*import" in body


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


def test_ri3c_guardrail_rejects_non_consumer_orchestration_inbound_normalizer():
    """非 consumers orchestration 文件持有 inbound normalizer 时 phase1 必须失败。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_ri3c_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports.event import WmsEventPort\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--phase", "phase1"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        "R-I3c 应拒绝非 consumers orchestration 文件持有 inbound normalizer\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/runtime/orchestration/services/_ri3c_violation_fixture.py" in result.stderr


def test_ri3c_guardrail_rejects_multiline_non_consumer_orchestration_import():
    """多行 import 的 inbound normalizer 也不能绕过 R-I3c。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_ri3c_multiline_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports.event import (\n"
        "    WmsEventPort,\n"
        ")\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--phase", "phase1"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"R-I3c 应拒绝多行 import 的 inbound normalizer\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/runtime/orchestration/services/_ri3c_multiline_violation_fixture.py" in result.stderr


def test_ri3c_guardrail_rejects_alias_qualified_inbound_normalizer_type_hint():
    """模块别名限定名和泛型 type hint 不能绕过 R-I3c。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_ri3c_alias_violation_fixture.py"
    fixture.write_text(
        "import src.app.wms_integration.ports.event as wms_events\n\n"
        "leaked_normalizers: list[wms_events.WmsEventPort] = []\n\n"
        "def get_normalizer() -> wms_events.WmsEventPort | None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--phase", "phase1"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        "R-I3c 应拒绝 alias-qualified/generic inbound normalizer type hint\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/runtime/orchestration/services/_ri3c_alias_violation_fixture.py" in result.stderr


def test_import_linter_config_exists():
    """.import-linter.ini 存在且包含 capability-isolation contract。"""
    ini = REPO_ROOT / ".import-linter.ini"
    assert ini.exists(), ".import-linter.ini 不存在"
    text = ini.read_text(encoding="utf-8")
    assert "[importlinter:contract:capability-isolation]" in text
    assert "type = forbidden" in text
    assert "forbidden_modules" in text
    assert "src.app.runtime.capability_port_registry" in text
    assert "src.app.runtime.inbound_normalizer_registry" in text


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
