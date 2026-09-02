"""Runtime Repository 不得反向依赖 Service 层。"""

from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_PACKAGE = "src.app.runtime.orchestration.repositories"
SERVICE_PACKAGE = "src.app.runtime.orchestration.services"


def _canonical_imports(source: str, *, filename: str = "<guardrail>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    modules: set[str] = set()
    package_parts = REPOSITORY_PACKAGE.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            if node.module:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
            continue

        parent_parts = package_parts[: len(package_parts) - node.level + 1]
        if node.module:
            modules.add(".".join((*parent_parts, *node.module.split("."))))
        else:
            modules.update(".".join((*parent_parts, alias.name)) for alias in node.names)
    return modules


def test_runtime_repositories_do_not_import_service_layer() -> None:
    repository_root = Path(__file__).resolve().parents[2] / "src/app/runtime/orchestration/repositories"
    expected_names = {
        "__init__.py",
        "material_unit_repository.py",
        "object_transition_event_repository.py",
        "rack_position_repository.py",
        "release_operational_readiness_repository.py",
        "runtime_location_event_repository.py",
        "session_mutation_repository.py",
        "session_repository.py",
        "timeline_sequence_repository.py",
    }
    repositories = sorted(repository_root.glob("*.py"))
    assert {repository.name for repository in repositories} == expected_names

    violations: list[str] = []
    for repository in repositories:
        imported_modules = _canonical_imports(repository.read_text(encoding="utf-8"), filename=str(repository))
        violations.extend(
            f"{repository.name}: {module}"
            for module in sorted(imported_modules)
            if module == SERVICE_PACKAGE or module.startswith(f"{SERVICE_PACKAGE}.")
        )

    assert violations == []


def test_runtime_repository_layering_scanner_rejects_absolute_and_relative_service_imports() -> None:
    imports = _canonical_imports(
        "from src.app.runtime.orchestration.services.query import TargetQuery\n"
        "from src.app.runtime.orchestration import services as svc\n"
        "from ..services.command import TargetCommand\n"
        "from .. import services\n"
        "from .session_repository import SessionRepository\n"
    )

    assert SERVICE_PACKAGE in imports
    assert f"{SERVICE_PACKAGE}.query" in imports
    assert f"{SERVICE_PACKAGE}.command" in imports
