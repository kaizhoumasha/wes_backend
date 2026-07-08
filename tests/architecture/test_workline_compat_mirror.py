"""workline compat mirror — workline 域工具镜像与 wlr 原文件 AST 签名一致。

不验证运行时行为, 只验证:
- src/app/workline/utils.py top-level 函数名自包含
- src/app/workline/trace_context.py TraceContext 类存在
- diagnostics 顶层门面导出 diagnostics 包所有公开符号

src/workline_runtime/ 删除后此测试改为自包含校验。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _public_symbols_from_module(ast_module: ast.Module) -> set[str]:
    """从 AST Module 中抽出 top-level 函数/类名 (不含以 _ 开头的私有符号)。"""
    symbols = set()
    for node in ast_module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            symbols.add(node.name)
    return symbols


def test_workline_utils_mirror_is_self_consistent_after_wlr_removal() -> None:
    """src/app/workline/utils.py 镜像在 wlr 物理删除后仍保持自包含。

    该契约覆盖 src.workline_runtime 删除后的正式实现。
    """
    mirror_path = REPO_ROOT / "src/app/workline/utils.py"
    assert mirror_path.exists(), f"镜像文件不存在: {mirror_path}"
    mirror_ast = ast.parse(mirror_path.read_text(encoding="utf-8"))
    public_symbols = _public_symbols_from_module(mirror_ast)
    assert public_symbols, "utils.py 镜像缺少任何 top-level 公开符号"


def test_workline_trace_context_mirror_exposes_tracecontext_class() -> None:
    """src/app/workline/trace_context.py 暴露 TraceContext 类。"""
    mirror_path = REPO_ROOT / "src/app/workline/trace_context.py"
    assert mirror_path.exists(), f"镜像文件不存在: {mirror_path}"
    ast_module = ast.parse(mirror_path.read_text(encoding="utf-8"))
    class_names = {
        node.name for node in ast_module.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert "TraceContext" in class_names, "trace_context 镜像缺少 TraceContext 类"


def test_diagnostics_bridge_re_exports_all_diagnostics_public_symbols() -> None:
    """diagnostics 顶层门面导出 diagnostics 包全部 16 个公开符号 (包括子模块)。"""
    from src.app.runtime.orchestration import diagnostics as diagnostics_bridge

    expected = {
        "DiagnosticCard",
        "DiagnosticCodeDefinition",
        "DiagnosticContext",
        "DiagnosticEvent",
        "ErrorCode",
        "ErrorDomain",
        "ProblemClass",
        "Recoverability",
        "Severity",
        "build_diagnostic_card",
        "build_diagnostic_context",
        "build_diagnostic_event",
        "error_domain_for",
        "get_diagnostic_code_definition",
        "list_diagnostic_code_definitions",
        "map_failure_to_diagnostic",
    }
    bridge_symbols = set(getattr(diagnostics_bridge, "__all__", [])) | {
        name for name in vars(diagnostics_bridge) if not name.startswith("_")
    }
    missing = expected - bridge_symbols
    assert not missing, f"diagnostics_bridge 缺少公开符号: {missing}"
