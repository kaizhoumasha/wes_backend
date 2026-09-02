"""WES 核心测试与二次开发 Adapter/插件测试所有权门禁。"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_TESTS_ROOT = REPO_ROOT / "tests"
CORE_ADAPTER_TEST_ROOT = CORE_TESTS_ROOT / "device_adapters"
CORE_PLUGIN_TEST_ROOT = CORE_TESTS_ROOT / "workline_plugins"
LEGACY_EXTENSION_TEST_ROOT = CORE_TESTS_ROOT / "workline_runtime" / "extensions"


def _secondary_import_roots() -> tuple[str, ...]:
    roots = {"device_adapters", "workline_plugins"}
    for pyproject_path in (REPO_ROOT / "workline_plugins").glob("*/pyproject.toml"):
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        for package_path in (
            pyproject.get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("packages", [])
        ):
            roots.add(Path(package_path).name)
    return tuple(sorted(roots))


SECONDARY_PACKAGE_ROOTS = _secondary_import_roots()


def _secondary_package_imports(path: Path) -> set[str]:
    """返回测试源码中对仓库根目录二次开发 Adapter/插件包的 import。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if any(alias.name == root or alias.name.startswith(f"{root}.") for root in SECONDARY_PACKAGE_ROOTS)
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and any(node.module == root or node.module.startswith(f"{root}.") for root in SECONDARY_PACKAGE_ROOTS)
        ):
            imports.add(node.module)
    return imports


def test_secondary_package_import_scanner_covers_plugin_and_adapter_roots(tmp_path: Path) -> None:
    source = tmp_path / "test_secondary_package_imports.py"
    source.write_text(
        "import workline_plugins.demo\n"
        "import rough_sorter.handlers\n"
        "import device_adapters.vendor\n"
        "from device_adapters.acme import Adapter\n",
        encoding="utf-8",
    )

    assert _secondary_package_imports(source) == {
        "device_adapters.acme",
        "device_adapters.vendor",
        "rough_sorter.handlers",
        "workline_plugins.demo",
    }


def test_core_test_tree_does_not_own_plugin_package_tests() -> None:
    assert not CORE_PLUGIN_TEST_ROOT.exists()


def test_core_test_tree_does_not_own_adapter_package_tests() -> None:
    assert not CORE_ADAPTER_TEST_ROOT.exists()


def test_core_test_tree_does_not_restore_legacy_extension_platform_tests() -> None:
    assert not any(LEGACY_EXTENSION_TEST_ROOT.rglob("test_*.py"))


def test_core_tests_do_not_import_secondary_development_plugin_packages() -> None:
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(found)
        for path in CORE_TESTS_ROOT.rglob("*.py")
        if (found := _secondary_package_imports(path))
    }

    assert offenders == {}


def test_core_tests_do_not_own_rough_sorter_business_files() -> None:
    assert list(CORE_TESTS_ROOT.rglob("test_*rough_sorter*.py")) == []


def test_core_production_package_does_not_embed_or_import_workline_plugins() -> None:
    embedded_root = REPO_ROOT / "src/app/runtime/workline_plugins"
    assert list(embedded_root.rglob("*.py")) == []

    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(found)
        for path in (REPO_ROOT / "src").rglob("*.py")
        if (found := _secondary_package_imports(path))
    }
    assert offenders == {}


def test_deployment_does_not_own_rough_sorter_business_modules() -> None:
    offenders = sorted(path.name for path in (REPO_ROOT / "deployment").glob("_rough_sorter_*.py"))

    assert offenders == []


def test_core_test_entrypoints_do_not_collect_or_map_secondary_plugin_packages() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = pyproject["tool"]["pytest"]["ini_options"]
    heavy_mapping = tomllib.loads((REPO_ROOT / "docs/architecture/heavy-test-impact.toml").read_text(encoding="utf-8"))
    mapped_paths = {
        path for mapping in heavy_mapping["mapping"] for path in (mapping["source_glob"], *mapping["heavy_tests"])
    }
    secondary_package_paths = {
        path
        for path in mapped_paths
        if any(path == root or path.startswith(f"{root}/") for root in SECONDARY_PACKAGE_ROOTS)
    }

    assert pytest_options["testpaths"] == ["tests"]
    assert "workline_plugins/**" in heavy_mapping["ignore_globs"]
    assert secondary_package_paths == set()
