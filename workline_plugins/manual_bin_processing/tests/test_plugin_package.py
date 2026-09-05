from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

from manual_bin_processing.plugin import PLUGIN_KEY, PLUGIN_VERSION, build_handlers

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src/manual_bin_processing"


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def test_plugin_decision_layer_has_only_sdk_and_stdlib_imports() -> None:
    config = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == ["wes-plugin-sdk==0.1.0"]

    allowed_roots = sys.stdlib_module_names | {"manual_bin_processing", "wes_plugin_sdk"}
    offenders = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            imported for imported in _absolute_imports(path) if imported.split(".", maxsplit=1)[0] not in allowed_roots
        )
        for path in (*SOURCE_ROOT.glob("*.py"), *(SOURCE_ROOT / "handlers").rglob("*.py"))
        if any(imported.split(".", maxsplit=1)[0] not in allowed_roots for imported in _absolute_imports(path))
    }
    assert offenders == {}


def test_plugin_entry_declares_key_and_version_with_no_handlers_yet() -> None:
    handlers = build_handlers()

    assert PLUGIN_KEY == "manual_bin_processing"
    assert PLUGIN_VERSION == "0.1.0"
    assert handlers == ()


def test_plugin_entry_has_no_registry_discovery_or_numbered_process_names() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE_ROOT.rglob("*.py"))

    assert "registry" not in source.lower()
    assert "discover" not in source.lower()
    assert re.search(r"Phase[0-9]+", source) is None
