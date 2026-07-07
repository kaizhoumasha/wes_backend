"""Phase5 legacy plugin absence guardrail.

Phase5 technical lane 的目标是退出旧 plugin runtime/import 框架。归档文档可以
提到旧路径，但生产 `src/` 路径不得再 import 旧模块。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = PROJECT_ROOT / "src"
FORBIDDEN_MODULES = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
)
FORBIDDEN_IMPORT_TEXT = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
)


@pytest.mark.parametrize("module_name", FORBIDDEN_MODULES)
def test_legacy_plugin_modules_are_not_importable_from_runtime_path(module_name: str) -> None:
    """旧 plugin runtime 路径必须离开生产 import surface。"""

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_production_source_does_not_reference_legacy_plugin_imports() -> None:
    """生产源码不得继续引用旧 plugin runtime 路径。"""

    offenders: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(forbidden in text for forbidden in FORBIDDEN_IMPORT_TEXT):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []
