"""Phase 2 runtime_status 归属收敛守护。

`WorkLine.runtime_status` 物理字段仍保留为兼容投影，目标态归属在
runtime/orchestration。WorkLine 配置域与 Phase4 capability 不能再把该字段
当作运行态事实直接读写；只读展示入口只能通过 projection snapshot 暴露。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECTION_SERVICE = Path("src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py")
READONLY_PROJECTION_VIEWS = {
    Path("src/app/runtime/orchestration/services/query/runtime_query_service.py"),
    Path("src/app/runtime/orchestration/services/trace/trace_query_service.py"),
}
OWNER_SENSITIVE_FILES = {
    Path("src/app/workline/services/safety_service.py"),
    Path("src/app/runtime/capabilities/phase4/start_admission_service.py"),
}
DOC_PATHS = {
    Path("docs/architecture/workline-and-plugin-restructuring.md"),
    Path("docs/architecture/legacy-cleanup-matrix.md"),
}


def _source(path: Path) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _is_runtime_status_attr(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "runtime_status"


def _is_projection_snapshot_attr(node: ast.Attribute) -> bool:
    if isinstance(node.value, ast.Name) and node.value.id.endswith("snapshot"):
        return True
    return (
        isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "runtime_status_snapshot"
    )


def _is_runtime_status_getattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "runtime_status"
    )


def _direct_runtime_status_reads(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if _is_runtime_status_getattr(node) or (
            _is_runtime_status_attr(node)
            and not isinstance(node.ctx, ast.Store)
            and not _is_projection_snapshot_attr(node)
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def _direct_runtime_status_writes(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = [node.target] if isinstance(node, ast.AnnAssign | ast.AugAssign) else list(node.targets)
            if any(_is_runtime_status_attr(target) for target in targets):
                lines.append(node.lineno)
    return sorted(set(lines))


def test_runtime_status_writes_are_centralized_in_projection_service() -> None:
    """runtime_status 直接写入只能出现在 runtime/orchestration 兼容投影服务。"""
    violations: list[str] = []
    for rel_path in sorted(Path("src/app").glob("**/*.py")):
        path = REPO_ROOT / rel_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel_path))
        if rel_path == PROJECTION_SERVICE:
            continue
        lines = _direct_runtime_status_writes(tree)
        violations.extend(f"{rel_path}:{line}" for line in lines)

    assert not violations, "WorkLine.runtime_status 直接写入必须集中到 projection service:\n  " + "\n  ".join(
        violations
    )


def test_workline_and_phase4_owner_sensitive_paths_use_projection_snapshot_for_runtime_status() -> None:
    """WorkLine safety 与 Phase4 START admission 不能直接读取 runtime_status 作归属判断。"""
    violations: list[str] = []
    for rel_path in sorted(OWNER_SENSITIVE_FILES):
        tree = ast.parse(_source(rel_path), filename=str(rel_path))
        lines = _direct_runtime_status_reads(tree)
        violations.extend(f"{rel_path}:{line}" for line in lines)

    assert not violations, (
        "归属敏感路径必须通过 WorkLineRuntimeStatusProjectionService snapshot/readiness 读取 runtime_status:\n  "
        + "\n  ".join(violations)
    )


def test_runtime_query_and_trace_only_expose_runtime_status_snapshot() -> None:
    """query/trace 只允许展示 projection snapshot，不直接把 WorkLine 字段当事实源。"""
    violations: list[str] = []
    for rel_path in sorted(READONLY_PROJECTION_VIEWS):
        tree = ast.parse(_source(rel_path), filename=str(rel_path))
        lines = _direct_runtime_status_reads(tree)
        violations.extend(f"{rel_path}:{line}" for line in lines)

    assert not violations, "query/trace 应通过 runtime_status_snapshot 暴露兼容字段:\n  " + "\n  ".join(violations)


def test_docs_do_not_describe_workline_runtime_status_as_state_owner() -> None:
    """文档不得把 WorkLine.runtime_status 描述为运行态事实归属。"""
    forbidden_phrases = (
        "WorkLine.runtime_status 状态 owner",
        "WorkLine.runtime_status 运行状态 owner",
        "WorkLine.runtime_status owner",
        "WorkLine.runtime_status 事实源",
        "WorkLine.runtime_status 权威",
        "runtime_status 状态 owner",
        "runtime_status 运行状态 owner",
        "runtime_status owner",
    )
    violations: list[str] = []
    for rel_path in sorted(DOC_PATHS):
        for lineno, line in enumerate(_source(rel_path).splitlines(), start=1):
            if any(phrase in line for phrase in forbidden_phrases):
                violations.append(f"{rel_path}:{lineno}: {line.strip()}")

    assert not violations, "文档必须描述为 runtime/orchestration compatibility projection:\n  " + "\n  ".join(
        violations
    )
