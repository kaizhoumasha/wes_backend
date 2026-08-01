"""WES 核心测试与二次开发插件测试所有权门禁。"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_TESTS_ROOT = REPO_ROOT / "tests"
CORE_PLUGIN_TEST_ROOT = CORE_TESTS_ROOT / "workline_plugins"


def _secondary_plugin_imports(path: Path) -> set[str]:
    """返回测试源码中对仓库根目录二次开发插件包的 import。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name == "workline_plugins" or alias.name.startswith("workline_plugins.")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "workline_plugins" or node.module.startswith("workline_plugins."))
        ):
            imports.add(node.module)
    return imports


def test_core_test_tree_does_not_own_plugin_package_tests() -> None:
    assert not CORE_PLUGIN_TEST_ROOT.exists()


def test_core_tests_do_not_import_secondary_development_plugin_packages() -> None:
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(found)
        for path in CORE_TESTS_ROOT.rglob("*.py")
        if (found := _secondary_plugin_imports(path))
    }

    assert offenders == {}


def test_core_test_entrypoints_do_not_collect_or_map_secondary_plugin_packages() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = pyproject["tool"]["pytest"]["ini_options"]
    heavy_mapping = (REPO_ROOT / "docs/architecture/heavy-test-impact.toml").read_text(encoding="utf-8")

    assert pytest_options["testpaths"] == ["tests"]
    assert "workline_plugins/" not in heavy_mapping


def test_governance_docs_publish_independent_plugin_test_ownership() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    test_guide = (CORE_TESTS_ROOT / "README.md").read_text(encoding="utf-8")

    for source in (agents, test_guide):
        assert "workline_plugins/<plugin_key>/" in source
        assert "tests/workline_plugins/" in source
        assert "核心" in source
