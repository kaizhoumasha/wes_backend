"""R-I3c inbound normalizer port 静态扫描器测试 (Phase 1 CEO-009 / Packet D / Phase 2 Step 4)。

主计划 §3.5.1 + H2: 业务 capability 不得持有 inbound normalizer Protocol
(WmsEventPort / DeviceEventPort / InboundEventPort) 或 RuntimeInbox / RuntimeInboxConsumer;
这些是 RuntimeInboxConsumer 专属依赖。

Phase 2 Step 4 扩展 SCAN_ROOTS 至 5 个域:
  - src/app/runtime
  - src/app/workline
  - src/app/callback
  - src/app/wms_integration/services
  - src/app/device
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_SCRIPT = REPO_ROOT / "scripts" / "architecture-guardrails.sh"

RI3C_TYPE_NAMES = ("WmsEventPort", "DeviceEventPort", "InboundEventPort", "RuntimeInbox", "RuntimeInboxConsumer")
RI3C_CONTEXT_NAMES = ("InboundNormalizerContext", "create_inbound_normalizer_context")
RI3C_SCAN_SCOPE = (
    "src/app/runtime",
    "src/app/workline",
    "src/app/callback",
    "src/app/wms_integration/services",
    "src/app/device",
)
RI3C_EXCLUDED_PATHS = (
    "src/app/wms_integration/ports/event.py",
    "src/app/wms_integration/ports/__init__.py",
    "src/app/runtime/capability_port_registry.py",
    "src/app/runtime/inbound_normalizer_registry.py",
    "src/app/contracts/external_contract_profile.py",
    "src/app/runtime/orchestration/__init__.py",
    "src/app/runtime/orchestration/runtime_inbox.py",
    "src/app/runtime/orchestration/consumers/",
)


def test_ri3c_rule_registered_in_guardrails_script():
    """architecture-guardrails.sh 包含 rule_ri3c 函数并加入调用链。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    assert "rule_ri3c" in text
    # 必须在主调用链里被调用
    assert "\nrule_ri3c\n" in text, "rule_ri3c 未在主调用链调用"


def test_ri3c_rule_scans_correct_paths():
    """rule_ri3c 必须扫描 5 个域 (Phase 2 Step 4 扩展后)。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    for scope in RI3C_SCAN_SCOPE:
        assert scope in text, f"rule_ri3c 缺扫描路径 {scope}"


def test_ri3c_rule_excludes_legitimate_holders():
    """rule_ri3c 必须排除合法的 inbound normalizer 持有者。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- R-I3c:", maxsplit=1)[1].split("# --- allowlist 校验 ---", maxsplit=1)[0]
    assert "src/app/runtime/orchestration/*" not in body, "R-I3c 禁止排除整个 orchestration 目录"
    for excluded in RI3C_EXCLUDED_PATHS:
        assert excluded in body, f"rule_ri3c 缺排除路径 {excluded}"


def test_ri3c_rule_pattern_covers_all_forbidden_inbound_names():
    """rule_ri3c 匹配 inbound normalizer 类型名和 runtime-only context 名。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    for name in (*RI3C_TYPE_NAMES, *RI3C_CONTEXT_NAMES):
        assert name in text, f"rule_ri3c pattern 缺禁止名称 {name}"
    assert "ast.ImportFrom" in text
    assert "ast.Import" in text


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
    """纯多行 import 的 inbound normalizer 也不能绕过 R-I3c。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_ri3c_multiline_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports.event import (\n"
        "    WmsEventPort,\n"
        ")\n\n"
        "normalizer = None\n"
        "_ = WmsEventPort\n",
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


def test_ri3c_guardrail_rejects_directory_prefix_allowlist_for_ri3c(tmp_path):
    """R-I3c 不允许用目录前缀 allowlist 吞掉未来 non-consumer orchestration 违规。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_ri3c_allowlist_violation_fixture.py"
    temp_allowlist = tmp_path / "architecture-guardrails.allowlist"
    temp_allowlist.write_text((REPO_ROOT / "scripts" / "architecture-guardrails.allowlist").read_text(encoding="utf-8"))
    with temp_allowlist.open("a", encoding="utf-8") as f:
        f.write(
            "R-I3c|src/app/runtime/orchestration|bad broad allowlist|2026-09-30|"
            "legacy:src/app/workline/repositories/debug_data_cleanup_repository.py:<file>#R-I3b|phase2\n"
        )
    fixture.write_text(
        "from src.app.wms_integration.ports.event import WmsEventPort\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--phase", "phase1", "--allowlist", str(temp_allowlist)],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"R-I3c 目录前缀 allowlist 应被拒绝, 不能覆盖违规文件\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "禁止目录前缀" in result.stderr


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


def test_ri3c_guardrail_rejects_qualified_runtime_reference():
    """普通表达式里的 module.WmsEventPort 引用也不能绕过 R-I3c。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_ri3c_reference_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports import event as wms_event_module\n\n"
        "normalizer_type = wms_event_module.WmsEventPort\n",
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
        f"R-I3c 应拒绝普通表达式引用 inbound normalizer 类型\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/runtime/orchestration/services/_ri3c_reference_violation_fixture.py" in result.stderr


def test_ri3c_guardrail_scans_callback_domain():
    """Phase 2 Step 4: R-I3c 必须扫描 src/app/callback 新域。"""
    fixture = REPO_ROOT / "src/app/callback/_ri3c_callback_violation_fixture.py"
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
        f"R-I3c 应拒绝 src/app/callback 内持有 inbound normalizer\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/callback/_ri3c_callback_violation_fixture.py" in result.stderr


def test_ri3c_guardrail_scans_wms_integration_services_domain():
    """Phase 2 Step 4: R-I3c 必须扫描 src/app/wms_integration/services 新域。"""
    fixture = REPO_ROOT / "src/app/wms_integration/services/_ri3c_wms_violation_fixture.py"
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
        "R-I3c 应拒绝 src/app/wms_integration/services 内持有 inbound normalizer\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/wms_integration/services/_ri3c_wms_violation_fixture.py" in result.stderr


def test_ri3c_guardrail_scans_device_domain():
    """Phase 2 Step 4: R-I3c 必须扫描 src/app/device 新域。"""
    fixture = REPO_ROOT / "src/app/device/_ri3c_device_violation_fixture.py"
    fixture.write_text(
        "from src.app.device.ports.event import DeviceEventPort\n\nleaked_normalizer: DeviceEventPort | None = None\n",
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
        f"R-I3c 应拒绝 src/app/device 内持有 inbound normalizer\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "R-I3c" in result.stderr
    assert "src/app/device/_ri3c_device_violation_fixture.py" in result.stderr


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
    assert "src.app.callback.models" in text
    assert "src.app.callback.repositories" in text
    assert "src.app.callback.v1" in text


def test_import_linter_check_script_disables_persistent_cache():
    """CI workspace 复用时 import-linter 必须避免读取旧 graph cache。"""
    script = REPO_ROOT / "scripts" / "import-linter-check.sh"
    text = script.read_text(encoding="utf-8")
    assert "--no-cache" in text


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


def test_import_linter_rejects_forbidden_callback_model_import_from_inbound_registry():
    """真实构造 forbidden import, 证明 import-linter contract 会失败。"""
    script = REPO_ROOT / "scripts" / "import-linter-check.sh"
    source = REPO_ROOT / "src/app/runtime/inbound_normalizer_registry.py"
    original = source.read_text(encoding="utf-8")
    source.write_text(
        original + "\nfrom src.app.callback.models import event as _ri3c_illegal_callback_event\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(script)],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        source.write_text(original, encoding="utf-8")

    assert result.returncode == 1, (
        "import-linter 应拒绝 inbound_normalizer_registry import callback model\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "callback.models" in result.stdout or "callback.models" in result.stderr
