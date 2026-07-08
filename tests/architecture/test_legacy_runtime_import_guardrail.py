"""LEGACY_RUNTIME_IMPORT src.workline_runtime production import 严格型 guardrail 测试。

主计划 §10.3 + Step 3: src.workline_runtime 在生产代码中**严禁**直接 import。
legacy runtime allowlist 使用严格型。
runtime 重构完成后,EXCLUDED_PREFIXES 收回至空集,consumers/ trust zone 退出。

历史过渡态:允许以下入口直接 import (作为单点过渡):
    1. src/app/runtime/orchestration/consumers/  (单点入口)
    2. tests/                                    (测试)
    3. migrations/                               (Alembic 数据迁移)
    4. src/workline_runtime/ 自身

runtime 重构后:
    1. tests/    (测试,允许)
    2. migrations/  (Alembic 数据迁移,允许)
    3. src/workline_runtime/ 自身 (历史, migration 后整目录已删)
其余 src/ 任何 production code 都不允许 import src.workline_runtime。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_SCRIPT = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

LEGACY_RUNTIME_IMPORT_PATTERN = re.compile(r"from src\.workline_runtime|import src\.workline_runtime")
# src.workline_runtime 删除后:consumers/ 退出 trust zone
LEGACY_RUNTIME_ALLOWED_PATHS = (
    "src/workline_runtime/",
    "tests/",
    "migrations/",
)


def test_legacy_runtime_import_rule_registered_in_guardrails_script():
    """architecture-guardrails.sh 包含 rule_legacy_runtime_import 函数并加入调用链。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    assert "rule_legacy_runtime_import" in text
    assert "\nrule_legacy_runtime_import\n" in text, "rule_legacy_runtime_import 未在主调用链调用"


def test_legacy_runtime_import_rule_pattern_matches_workline_runtime_imports():
    """rule_legacy_runtime_import pattern 必须覆盖 from/import src.workline_runtime。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    # 必须在 rule_legacy_runtime_import 函数体内出现
    body = text.split("# --- LEGACY_RUNTIME_IMPORT:", maxsplit=1)[1].split(
        "# --- CAPABILITY_IMPLEMENTATION_IMPORT:", maxsplit=1
    )[0]
    assert "src\\.workline_runtime" in body, "rule_legacy_runtime_import pattern 缺 src.workline_runtime 字面量"


def test_legacy_runtime_import_rule_excludes_legitimate_importers():
    """rule_legacy_runtime_import 必须排除合法 importer。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- LEGACY_RUNTIME_IMPORT:", maxsplit=1)[1].split(
        "# --- CAPABILITY_IMPLEMENTATION_IMPORT:", maxsplit=1
    )[0]
    for excluded in LEGACY_RUNTIME_ALLOWED_PATHS:
        assert excluded in body, f"rule_legacy_runtime_import 缺排除路径 {excluded}"


def test_legacy_runtime_import_allowlist_entries_have_legacy_entry_id_and_drop_phase():
    """LEGACY_RUNTIME_IMPORT allowlist 字段契约校验 + 终态 (LEGACY_RUNTIME_IMPORT=0) 允许。

    legacy runtime import cleanup 后 LEGACY_RUNTIME_IMPORT allowlist 终态 = 0, 本测试需支持两种状态:
    1. LEGACY_RUNTIME_IMPORT > 0: 每条 legacy_entry_id + drop_phase 字段非空
    2. LEGACY_RUNTIME_IMPORT = 0: 直接通过 (runtime 重构完成)
    """
    if not ALLOWLIST.exists():
        return
    legacy_runtime_import_lines = [
        line for line in ALLOWLIST.read_text().splitlines() if line.startswith("LEGACY_RUNTIME_IMPORT|")
    ]
    if not legacy_runtime_import_lines:
        # src.workline_runtime allowlist 清零后,字段契约不适用。
        return
    for line in legacy_runtime_import_lines:
        fields = line.split("|")
        # fields: prefix, file, reason, expires, legacy_entry_id, rule suffix, drop_phase
        assert len(fields) >= 7, f"LEGACY_RUNTIME_IMPORT 行字段不全: {line}"
        assert fields[4] != "<file>", f"legacy_entry_id 未填: {line}"
        assert fields[6].startswith("phase"), f"drop_phase 格式错: {line}"


def test_legacy_runtime_import_guardrail_runs_clean_in_enforced_mode():
    """enforced 模式运行 architecture-guardrails.sh, 当前代码无未覆盖违规。"""
    result = subprocess.run(
        ["bash", str(GUARDRAILS_SCRIPT), "--mode", "enforced"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"architecture-guardrails.sh enforced exit={result.returncode}\n"
        f"stdout last 30 lines:\n{chr(10).join(result.stdout.splitlines()[-30:])}\n"
        f"stderr last 30 lines:\n{chr(10).join(result.stderr.splitlines()[-30:])}"
    )


def test_legacy_runtime_import_production_imports_all_have_allowlist_coverage():
    """production code 内的 src.workline_runtime import 必须有 allowlist 覆盖。

    src.workline_runtime 删除后:consumers/ 已退出 trust zone,本测试不应再有未覆盖违规。
    """
    src_root = REPO_ROOT / "src"
    allowlisted_paths = set()
    if ALLOWLIST.exists():
        for line in ALLOWLIST.read_text().splitlines():
            if line.startswith("LEGACY_RUNTIME_IMPORT|"):
                parts = line.split("|")
                if len(parts) >= 2:
                    allowlisted_paths.add(parts[1])

    offenders = []
    for py_file in src_root.rglob("*.py"):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/workline_runtime/"):
            continue
        # src.workline_runtime 删除后:consumers/ 不再是 trust zone
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[warn] skip unreadable {rel}: {exc}")
            continue
        if LEGACY_RUNTIME_IMPORT_PATTERN.search(content) and rel not in allowlisted_paths:
            offenders.append(rel)

    assert not offenders, (
        "以下 production 文件 import src.workline_runtime 但未在 LEGACY_RUNTIME_IMPORT allowlist:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_runtime_inbox_consumer_compiles() -> None:
    """RuntimeInboxConsumer 模块可被 import (consumers/ 入口仍存在)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    assert RuntimeInboxConsumer is not None


# --- src.workline_runtime 删除后测试 ---


def test_excluded_prefixes_does_not_contain_consumers():
    """src.workline_runtime 删除后:EXCLUDED_PREFIXES 不再含 consumers/ (trust zone 退出)。

    consumers/ 内 RuntimeInboxConsumer 已 legacy runtime-free,无需 EXCLUDED_PREFIXES 保护。
    """
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- LEGACY_RUNTIME_IMPORT:", maxsplit=1)[1].split(
        "# --- CAPABILITY_IMPLEMENTATION_IMPORT:", maxsplit=1
    )[0]
    assert "src/app/runtime/orchestration/consumers/" not in body, (
        "src.workline_runtime 删除后:EXCLUDED_PREFIXES 不应再含 consumers/ trust zone"
    )


def test_no_consumers_in_legacy_runtime_allowed_paths():
    """LEGACY_RUNTIME_ALLOWED_PATHS 模块常量不再含 consumers/。"""
    assert "src/app/runtime/orchestration/consumers/" not in LEGACY_RUNTIME_ALLOWED_PATHS, (
        "src.workline_runtime 删除后:LEGACY_RUNTIME_ALLOWED_PATHS 不应再含 consumers/"
    )


def test_consumers_directory_still_exists():
    """consumers/ 目录在旧 runtime 入口删除后仍存在 (RuntimeInboxConsumer 单点入口保留)。"""
    consumers_dir = REPO_ROOT / "src" / "app" / "runtime" / "orchestration" / "consumers"
    assert consumers_dir.is_dir(), "consumers/ 目录应保留 (RuntimeInboxConsumer)"
    init_file = consumers_dir / "__init__.py"
    assert init_file.is_file(), "consumers/__init__.py 应保留"
    consumer_module = consumers_dir / "runtime_inbox_consumer.py"
    assert consumer_module.is_file(), "consumers/runtime_inbox_consumer.py 应保留"
