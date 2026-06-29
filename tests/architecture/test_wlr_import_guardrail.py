"""R-WLR src.workline_runtime production import 严格型 guardrail 测试 (Phase 2 launch PR)。

主计划 §10.3 + Step 3: src.workline_runtime 在生产代码中仅允许以下入口直接 import:
    1. src/app/runtime/orchestration/consumers/  (单点入口)
    2. tests/                                    (测试)
    3. migrations/                               (Alembic 数据迁移)
    4. src/workline_runtime/ 自身

其余 src/ 任何 production code 都不允许 import src.workline_runtime (wlr allowlist 严格型)。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_SCRIPT = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

WLR_IMPORT_PATTERN = re.compile(r"from src\.workline_runtime|import src\.workline_runtime")
WLR_ALLOWED_PATHS = (
    "src/workline_runtime/",
    "src/app/runtime/orchestration/consumers/",
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
    """rule_wlr_import 必须排除 wlr 自身 + consumers + tests + migrations。"""
    text = GUARDRAILS_SCRIPT.read_text(encoding="utf-8")
    body = text.split("# --- R-WLR:", maxsplit=1)[1].split("# --- R-I3b:", maxsplit=1)[0]
    for excluded in WLR_ALLOWED_PATHS:
        assert excluded in body, f"rule_wlr_import 缺排除路径 {excluded}"


def test_wlr_allowlist_entries_have_legacy_entry_id_and_drop_phase():
    """R-WLR allowlist 每条记录都必须有 legacy_entry_id + drop_phase 字段 (Phase 2 launch PR)。"""
    if not ALLOWLIST.exists():
        return
    rows = [line for line in ALLOWLIST.read_text().splitlines() if line.startswith("R-WLR|")]
    assert rows, "R-WLR allowlist 必须至少有一条记录"
    for row in rows:
        parts = row.split("|")
        assert len(parts) >= 6, f"R-WLR allowlist 字段不足 6 列: {row}"
        legacy_entry_id = parts[4]
        drop_phase = parts[5]
        assert legacy_entry_id.startswith("legacy:"), f"legacy_entry_id 缺 legacy: 前缀: {row}"
        assert drop_phase, f"drop_phase 必填: {row}"
        assert drop_phase == "phase2", f"Phase 2 launch PR R-WLR drop_phase 必须 = phase2 (实际={drop_phase}): {row}"


def test_wlr_guardrail_runs_clean_in_phase1():
    """phase1 模式运行 architecture-guardrails.sh, 当前代码无 R-WLR 未覆盖违规 (退出码 0)。"""
    result = subprocess.run(
        ["bash", str(GUARDRAILS_SCRIPT), "--phase", "phase1"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"architecture-guardrails.sh phase1 exit={result.returncode}\n"
        f"stdout last 30 lines:\n{chr(10).join(result.stdout.splitlines()[-30:])}\n"
        f"stderr last 30 lines:\n{chr(10).join(result.stderr.splitlines()[-30:])}"
    )


def test_wlr_production_imports_all_have_allowlist_coverage():
    """所有 production code 内的 src.workline_runtime import 都必须在 R-WLR allowlist 覆盖范围。"""
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
        if rel.startswith("src/app/runtime/orchestration/consumers/"):
            continue
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
    """RuntimeInboxConsumer 模块可被 import (consumers/ 单点入口存在)"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    assert RuntimeInboxConsumer is not None
