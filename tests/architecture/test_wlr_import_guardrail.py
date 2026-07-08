"""R-WLR src.workline_runtime production import 严格型 guardrail 测试 (Phase 2 launch + Stage 3)。

主计划 §10.3 + Step 3: src.workline_runtime 在生产代码中**严禁**直接 import (wlr allowlist 严格型)。
runtime migration 完成后,EXCLUDED_PREFIXES 收回至空集,consumers/ trust zone 退出。

历史 (阶段 2 launch PR 末态):允许以下入口直接 import (作为单点过渡):
    1. src/app/runtime/orchestration/consumers/  (单点入口)
    2. tests/                                    (测试)
    3. migrations/                               (Alembic 数据迁移)
    4. src/workline_runtime/ 自身

阶段 3 后:
    1. tests/    (测试,允许)
    2. migrations/  (Alembic 数据迁移,允许)
    3. src/workline_runtime/ 自身 (历史,阶段 3 后整目录已删)
其余 src/ 任何 production code 都不允许 import src.workline_runtime。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_SCRIPT = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

WLR_IMPORT_PATTERN = re.compile(r"from src\.workline_runtime|import src\.workline_runtime")
# 阶段 3 终态:consumers/ 退出 trust zone
WLR_ALLOWED_PATHS = (
    "src/workline_runtime/",
    "tests/",
    "migrations/",
)


def test_wlr_rule_registered_in_guardrails_script():
    """architecture-guardrails.sh 包含 rule_wlr_import 函数并加入调用链。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    assert "rule_wlr_import" in text
    assert "\nrule_wlr_import\n" in text, "rule_wlr_import 未在主调用链调用"


def test_wlr_rule_pattern_matches_workline_runtime_imports():
    """rule_wlr_import pattern 必须覆盖 from/import src.workline_runtime。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    # 必须在 rule_wlr_import 函数体内出现
    body = text.split("# --- R-WLR:", maxsplit=1)[1].split("# --- R-I3b:", maxsplit=1)[0]
    assert "src\\.workline_runtime" in body, "rule_wlr_import pattern 缺 src.workline_runtime 字面量"


def test_wlr_rule_excludes_legitimate_importers():
    """rule_wlr_import 必须排除 wlr 自身 + tests + migrations (阶段 3 终态)。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- R-WLR:", maxsplit=1)[1].split("# --- R-I3b:", maxsplit=1)[0]
    for excluded in WLR_ALLOWED_PATHS:
        assert excluded in body, f"rule_wlr_import 缺排除路径 {excluded}"


def test_wlr_allowlist_entries_have_legacy_entry_id_and_drop_phase():
    """R-WLR allowlist 字段契约校验 + 终态 (R-WLR=0) 允许。

    C5b 后 R-WLR allowlist 终态 = 0, 本测试需支持两种状态:
    1. R-WLR > 0: 每条 legacy_entry_id + drop_phase 字段非空
    2. R-WLR = 0: 直接通过 (runtime migration 完成)
    """
    if not ALLOWLIST.exists():
        return
    rwlr_lines = [line for line in ALLOWLIST.read_text().splitlines() if line.startswith("R-WLR|")]
    if not rwlr_lines:
        # 阶段 2 终态: R-WLR allowlist 清零, 字段契约不适用
        return
    for line in rwlr_lines:
        fields = line.split("|")
        # fields: [prefix, file, reason, expires, legacy_entry_id, "#R-WLR", drop_phase]
        assert len(fields) >= 7, f"R-WLR 行字段不全: {line}"
        assert fields[4] != "<file>", f"legacy_entry_id 未填: {line}"
        assert fields[6].startswith("phase"), f"drop_phase 格式错: {line}"


def test_wlr_guardrail_runs_clean_in_enforced_mode():
    """enforced 模式运行 architecture-guardrails.sh, 当前代码无 R-WLR 未覆盖违规 (退出码 0)。"""
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


def test_wlr_production_imports_all_have_allowlist_coverage():
    """所有 production code 内的 src.workline_runtime import 都必须在 R-WLR allowlist 覆盖范围。

    阶段 3 终态:consumers/ 已退出 trust zone,本测试不应再有未覆盖违规。
    """
    src_root = REPO_ROOT / "src"
    allowlisted_paths = set()
    if ALLOWLIST.exists():
        for line in ALLOWLIST.read_text().splitlines():
            if line.startswith("R-WLR|"):
                parts = line.split("|")
                if len(parts) >= 2:
                    allowlisted_paths.add(parts[1])

    offenders = []
    for py_file in src_root.rglob("*.py"):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/workline_runtime/"):
            continue
        # 阶段 3 终态:consumers/ 不再是 trust zone
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[warn] skip unreadable {rel}: {exc}")
            continue
        if WLR_IMPORT_PATTERN.search(content) and rel not in allowlisted_paths:
            offenders.append(rel)

    assert not offenders, "以下 production 文件 import src.workline_runtime 但未在 R-WLR allowlist:\n  " + "\n  ".join(
        sorted(offenders)
    )


def test_runtime_inbox_consumer_compiles() -> None:
    """RuntimeInboxConsumer 模块可被 import (consumers/ 入口仍存在)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    assert RuntimeInboxConsumer is not None


# --- 阶段 3 新增测试 ---


def test_excluded_prefixes_does_not_contain_consumers():
    """阶段 3 终态:EXCLUDED_PREFIXES 不再含 consumers/ (trust zone 退出)。

    consumers/ 内 RuntimeInboxConsumer 已 wlr-free,无需 EXCLUDED_PREFIXES 保护。
    """
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- R-WLR:", maxsplit=1)[1].split("# --- R-I3b:", maxsplit=1)[0]
    assert "src/app/runtime/orchestration/consumers/" not in body, (
        "阶段 3 终态:EXCLUDED_PREFIXES 不应再含 consumers/ trust zone"
    )


def test_no_consumers_in_wlr_allowed_paths():
    """WLR_ALLOWED_PATHS 模块常量不再含 consumers/。"""
    assert "src/app/runtime/orchestration/consumers/" not in WLR_ALLOWED_PATHS, (
        "阶段 3 终态:WLR_ALLOWED_PATHS 不应再含 consumers/"
    )


def test_consumers_directory_still_exists():
    """consumers/ 目录在阶段 3 后仍存在 (RuntimeInboxConsumer 单点入口保留)。"""
    consumers_dir = REPO_ROOT / "src" / "app" / "runtime" / "orchestration" / "consumers"
    assert consumers_dir.is_dir(), "consumers/ 目录应保留 (RuntimeInboxConsumer)"
    init_file = consumers_dir / "__init__.py"
    assert init_file.is_file(), "consumers/__init__.py 应保留"
    consumer_module = consumers_dir / "runtime_inbox_consumer.py"
    assert consumer_module.is_file(), "consumers/runtime_inbox_consumer.py 应保留"
