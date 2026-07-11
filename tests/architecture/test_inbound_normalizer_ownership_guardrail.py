"""INBOUND_NORMALIZER_OWNERSHIP inbound normalizer port 静态扫描器测试。

主计划 §3.5.1 + H2: 业务 capability 不得持有 inbound normalizer Protocol
(WmsEventPort / DeviceEventPort / InboundEventPort) 或 RuntimeInbox；这些依赖只属于
专用 normalization wiring 与 RuntimeInbox processor 链路。

当前 SCAN_ROOTS 覆盖 5 个域:
  - src/app/runtime
  - src/app/workline
  - src/app/callback
  - src/app/wms_integration/services
  - src/app/device
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_SCRIPT = REPO_ROOT / "scripts" / "architecture-guardrails.sh"

INBOUND_NORMALIZER_OWNERSHIP_TYPE_NAMES = (
    "WmsEventPort",
    "DeviceEventPort",
    "InboundEventPort",
    "RuntimeInbox",
)
INBOUND_NORMALIZER_OWNERSHIP_SCAN_SCOPE = (
    "src/app/runtime",
    "src/app/workline",
    "src/app/callback",
    "src/app/wms_integration/services",
    "src/app/device",
)
INBOUND_NORMALIZER_OWNERSHIP_EXCLUDED_PATHS = (
    "src/app/wms_integration/ports/event.py",
    "src/app/wms_integration/ports/__init__.py",
    "src/app/runtime/inbound_normalizer_registry.py",
    "src/app/contracts/external_contract_profile.py",
    "src/app/runtime/orchestration/__init__.py",
    "src/app/runtime/orchestration/runtime_inbox.py",
    "src/app/runtime/orchestration/repositories/runtime_inbox_claim_repository.py",
    "src/app/runtime/orchestration/repositories/runtime_inbox_repository.py",
    "src/app/runtime/orchestration/consumers/runtime_inbox_service.py",
)


def test_inbound_normalizer_ownership_rule_registered_in_guardrails_script():
    """architecture-guardrails.sh 包含 rule_inbound_normalizer_ownership 函数并加入调用链。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    assert "rule_inbound_normalizer_ownership" in text
    # 必须在主调用链里被调用
    assert "\nrule_inbound_normalizer_ownership\n" in text, "rule_inbound_normalizer_ownership 未在主调用链调用"


def test_inbound_normalizer_ownership_rule_scans_correct_paths():
    """rule_inbound_normalizer_ownership 必须扫描 5 个域。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    for scope in INBOUND_NORMALIZER_OWNERSHIP_SCAN_SCOPE:
        assert scope in text, f"rule_inbound_normalizer_ownership 缺扫描路径 {scope}"


def test_inbound_normalizer_ownership_rule_excludes_legitimate_holders():
    """rule_inbound_normalizer_ownership 必须排除合法的 inbound normalizer 持有者。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- INBOUND_NORMALIZER_OWNERSHIP:", maxsplit=1)[1].split(
        "# --- allowlist 校验 ---", maxsplit=1
    )[0]
    assert "src/app/runtime/orchestration/*" not in body, "INBOUND_NORMALIZER_OWNERSHIP 禁止排除整个 orchestration 目录"
    for excluded in INBOUND_NORMALIZER_OWNERSHIP_EXCLUDED_PATHS:
        assert excluded in body, f"rule_inbound_normalizer_ownership 缺排除路径 {excluded}"


def test_inbound_normalizer_ownership_rule_pattern_covers_all_forbidden_inbound_names():
    """rule_inbound_normalizer_ownership 匹配 inbound normalizer 禁用名称。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    for name in INBOUND_NORMALIZER_OWNERSHIP_TYPE_NAMES:
        assert name in text, f"rule_inbound_normalizer_ownership pattern 缺禁止名称 {name}"
    assert "ast.ImportFrom" in text
    assert "ast.Import" in text


def test_inbound_normalizer_ownership_guardrail_runs_clean_in_enforced_mode():
    """enforced 模式运行 architecture-guardrails.sh, 当前代码无 ownership 违规。"""
    # NOTE: brief had `sys.executable` here, but architecture-guardrails.sh has
    # `#!/usr/bin/env bash` shebang and is not a Python script. Use bash.
    result = subprocess.run(
        ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # 当前所有 inbound normalizer 类型持有者都在 allowlist 之外但命中 exclusion
    # 或不存在 capability 路径 import, 应返回 0
    assert result.returncode == 0, (
        f"architecture-guardrails.sh enforced exit={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_inbound_normalizer_ownership_guardrail_rejects_non_consumer_orchestration_inbound_normalizer():
    """非 consumers orchestration 文件持有 inbound normalizer 时 enforced 模式必须失败。"""
    fixture = REPO_ROOT / "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports.event import WmsEventPort\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        "INBOUND_NORMALIZER_OWNERSHIP 应拒绝非 consumers orchestration 文件持有 inbound normalizer\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_violation_fixture.py" in result.stderr


def test_inbound_normalizer_ownership_guardrail_rejects_multiline_non_consumer_orchestration_import():
    """纯多行 import 的 inbound normalizer 也不能绕过 INBOUND_NORMALIZER_OWNERSHIP。"""
    fixture = (
        REPO_ROOT
        / "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_multiline_violation_fixture.py"
    )
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
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"INBOUND_NORMALIZER_OWNERSHIP 应拒绝多行 import 的 inbound normalizer\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert (
        "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_multiline_violation_fixture.py"
        in result.stderr
    )


def test_inbound_normalizer_ownership_guardrail_rejects_directory_prefix_allowlist_for_inbound_normalizer_ownership(
    tmp_path,
):
    """目录前缀 allowlist 不得吞掉未来 non-consumer orchestration 违规。"""
    fixture = (
        REPO_ROOT
        / "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_allowlist_violation_fixture.py"
    )
    temp_allowlist = tmp_path / "architecture-guardrails.allowlist"
    temp_allowlist.write_text((REPO_ROOT / "scripts" / "architecture-guardrails.allowlist").read_text(encoding="utf-8"))
    with temp_allowlist.open("a", encoding="utf-8") as f:
        f.write(
            "INBOUND_NORMALIZER_OWNERSHIP|src/app/runtime/orchestration|bad broad allowlist|2026-09-30|"
            "legacy:src/app/workline/repositories/debug_data_cleanup_repository.py:<file>#CAPABILITY_IMPLEMENTATION_IMPORT|phase"
            "2\n"
        )
    fixture.write_text(
        "from src.app.wms_integration.ports.event import WmsEventPort\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced", "--allowlist", str(temp_allowlist)],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"INBOUND_NORMALIZER_OWNERSHIP 目录前缀 allowlist 应被拒绝, 不能覆盖违规文件\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert "禁止目录前缀" in result.stderr


def test_inbound_normalizer_ownership_guardrail_rejects_alias_qualified_inbound_normalizer_type_hint():
    """模块别名限定名和泛型 type hint 不能绕过 INBOUND_NORMALIZER_OWNERSHIP。"""
    fixture = (
        REPO_ROOT / "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_alias_violation_fixture.py"
    )
    fixture.write_text(
        "import src.app.wms_integration.ports.event as wms_events\n\n"
        "leaked_normalizers: list[wms_events.WmsEventPort] = []\n\n"
        "def get_normalizer() -> wms_events.WmsEventPort | None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        "INBOUND_NORMALIZER_OWNERSHIP 应拒绝 alias-qualified/generic inbound normalizer type hint\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert (
        "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_alias_violation_fixture.py"
        in result.stderr
    )


def test_inbound_normalizer_ownership_guardrail_rejects_qualified_runtime_reference():
    """普通表达式里的 module.WmsEventPort 引用也不能绕过 INBOUND_NORMALIZER_OWNERSHIP。"""
    fixture = (
        REPO_ROOT
        / "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_reference_violation_fixture.py"
    )
    fixture.write_text(
        "from src.app.wms_integration.ports import event as wms_event_module\n\n"
        "normalizer_type = wms_event_module.WmsEventPort\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"INBOUND_NORMALIZER_OWNERSHIP 应拒绝普通表达式引用 inbound normalizer 类型\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert (
        "src/app/runtime/orchestration/services/_inbound_normalizer_ownership_reference_violation_fixture.py"
        in result.stderr
    )


def test_inbound_normalizer_ownership_guardrail_scans_callback_domain():
    """INBOUND_NORMALIZER_OWNERSHIP 必须扫描 src/app/callback 域。"""
    fixture = REPO_ROOT / "src/app/callback/_inbound_normalizer_ownership_callback_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports.event import WmsEventPort\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"INBOUND_NORMALIZER_OWNERSHIP 应拒绝 src/app/callback 内持有 inbound normalizer\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert "src/app/callback/_inbound_normalizer_ownership_callback_violation_fixture.py" in result.stderr


def test_inbound_normalizer_ownership_guardrail_scans_wms_integration_services_domain():
    """INBOUND_NORMALIZER_OWNERSHIP 必须扫描 src/app/wms_integration/services 域。"""
    fixture = REPO_ROOT / "src/app/wms_integration/services/_inbound_normalizer_ownership_wms_violation_fixture.py"
    fixture.write_text(
        "from src.app.wms_integration.ports.event import WmsEventPort\n\n"
        "leaked_normalizer: WmsEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        "INBOUND_NORMALIZER_OWNERSHIP 应拒绝 src/app/wms_integration/services 内持有 inbound normalizer\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert "src/app/wms_integration/services/_inbound_normalizer_ownership_wms_violation_fixture.py" in result.stderr


def test_inbound_normalizer_ownership_guardrail_scans_device_domain():
    """INBOUND_NORMALIZER_OWNERSHIP 必须扫描 src/app/device 域。"""
    fixture = REPO_ROOT / "src/app/device/_inbound_normalizer_ownership_device_violation_fixture.py"
    fixture.write_text(
        "from src.app.device.ports.event import DeviceEventPort\n\nleaked_normalizer: DeviceEventPort | None = None\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)

    assert result.returncode == 1, (
        f"INBOUND_NORMALIZER_OWNERSHIP 应拒绝 src/app/device 内持有 inbound normalizer\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "INBOUND_NORMALIZER_OWNERSHIP" in result.stderr
    assert "src/app/device/_inbound_normalizer_ownership_device_violation_fixture.py" in result.stderr


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


def test_import_linter_check_script_uses_lint_imports_when_uv_is_unavailable(tmp_path):
    """CI testing 镜像只提供 venv CLI 时, 脚本仍应直接运行 lint-imports。"""
    script = REPO_ROOT / "scripts" / "import-linter-check.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_file = tmp_path / "lint-imports.args"
    lint_imports = fake_bin / "lint-imports"
    lint_imports.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > {args_file}\nexit 0\n",
        encoding="utf-8",
    )
    lint_imports.chmod(0o755)

    env = {**os.environ, "PATH": f"{fake_bin}:/bin:/usr/bin"}
    result = subprocess.run(
        ["/bin/bash", str(script)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args == ["--config", ".import-linter.ini", "--no-cache"]


def test_import_linter_is_installed_in_ci_dependency_group():
    """Jenkins testing 镜像使用 ci group, 必须安装 lint-imports CLI。"""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ci_dependencies = pyproject["dependency-groups"]["ci"]
    assert any(dependency.startswith("import-linter") for dependency in ci_dependencies)


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
        original
        + "\nfrom src.app.callback.models import event as _inbound_normalizer_ownership_illegal_callback_event\n",
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
