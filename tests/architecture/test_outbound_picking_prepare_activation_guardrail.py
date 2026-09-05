"""outbound PickingTask prepare 暗构建不得被生产入口提前激活。"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARE_SYMBOLS = {"PickingTaskPrepareAdapter", "PickingTaskPrepareService"}
ALLOWED_PREFIXES = (
    "src/app/wms_adapter/outbound_picking/",
    "src/app/wms_integration/outbound_picking/",
)


def _activation_references(source: str, *, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases: set[str] = set()
    module_aliases: set[str] = set()
    references: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in PREPARE_SYMBOLS:
                    aliases.add(imported.asname or imported.name)
                    references.append(f"{filename}:{node.lineno}:import:{imported.name}")
                elif imported.name in {"outbound_picking", "services"}:
                    module_aliases.add(imported.asname or imported.name)
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if "outbound_picking" in imported.name:
                    module_aliases.add(imported.asname or imported.name.split(".", maxsplit=1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in aliases:
            references.append(f"{filename}:{node.lineno}:construct:{node.func.id}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in module_aliases
            and node.func.attr in PREPARE_SYMBOLS
        ):
            references.append(f"{filename}:{node.lineno}:construct:{node.func.value.id}.{node.func.attr}")
    return references


def test_alias_import_and_indirect_construction_fixture_are_detected() -> None:
    references = _activation_references(
        "from src.app.wms_integration.outbound_picking.services import "
        "PickingTaskPrepareService as Builder\nBuilder(factory, task_queue_gateway=queue)\n",
        filename="fixture.py",
    )

    assert references == [
        "fixture.py:1:import:PickingTaskPrepareService",
        "fixture.py:2:construct:Builder",
    ]

    module_references = _activation_references(
        "import src.app.wms_integration.outbound_picking.services as picking\n"
        "picking.PickingTaskPrepareService(factory, task_queue_gateway=queue)\n",
        filename="module_fixture.py",
    )
    assert module_references == ["module_fixture.py:2:construct:picking.PickingTaskPrepareService"]


def test_prepare_has_no_production_activation_or_execution_reverse_import() -> None:
    violations: list[str] = []
    roots = [REPO_ROOT / "main.py", *(REPO_ROOT / root_name for root_name in ("src", "workline_plugins"))]
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*.py")
        for path in paths:
            relative = path.relative_to(REPO_ROOT).as_posix()
            if any(part.startswith(".") for part in path.relative_to(REPO_ROOT).parts):
                continue
            source = path.read_text(encoding="utf-8")
            if not relative.startswith(ALLOWED_PREFIXES):
                violations.extend(_activation_references(source, filename=relative))
            if relative.startswith("src/app/execution/"):
                tree = ast.parse(source, filename=relative)
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.ImportFrom)
                        and node.module
                        and node.module.startswith("src.app.wms_integration.outbound_picking")
                    ):
                        violations.append(f"{relative}:{node.lineno}:reverse-import:{node.module}")
                    elif isinstance(node, ast.Import):
                        for imported in node.names:
                            if imported.name.startswith("src.app.wms_integration.outbound_picking"):
                                violations.append(f"{relative}:{node.lineno}:reverse-import:{imported.name}")

    assert violations == []
