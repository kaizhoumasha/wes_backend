"""Transport 基础能力测试不得直接依赖业务、设备或插件实现。"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_TEST_ROOTS = (
    REPO_ROOT / "tests/runtime/transport",
    REPO_ROOT / "tests/contracts/wms_adapter",
    REPO_ROOT / "tests/integration/transport",
)
FORBIDDEN_IMPORT_PREFIXES = (
    "src.app.device",
    "src.app.workline",
    "src.app.picking",
    "workline_plugins",
)


def test_transport_tests_do_not_directly_import_business_device_or_plugin_implementations() -> None:
    violations: list[str] = []

    for root in TRANSPORT_TEST_ROOTS:
        for path in sorted(root.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: tuple[str, ...]
                if isinstance(node, ast.Import):
                    modules = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = (node.module,)
                else:
                    continue
                for module in modules:
                    if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {module}")

    assert violations == []
