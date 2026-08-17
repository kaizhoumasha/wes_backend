from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

from conftest import FakeEpochReader, FakeExecutionReader, FakePositionReader, epoch_snapshot, execution_snapshot
from wes_plugin_sdk import FactReference, HandlerMetadata

from rough_sorter.facts import (
    AdmissionDecidedFact,
    DevicePositionConfirmedFact,
    MaterialEvidenceReadyFact,
    PlacementCompletedFact,
    RecoveryDecidedFact,
    ReplacementPlanDecidedFact,
    TargetDecidedFact,
    TransportOutcomePublishedFact,
)
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION, build_handlers

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src/rough_sorter"


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def test_plugin_has_only_sdk_runtime_dependency_and_stdlib_source_imports() -> None:
    config = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == ["wes-plugin-sdk==0.1.0"]

    allowed_roots = sys.stdlib_module_names | {"rough_sorter", "wes_plugin_sdk"}
    offenders = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            imported for imported in _absolute_imports(path) if imported.split(".", maxsplit=1)[0] not in allowed_roots
        )
        for path in SOURCE_ROOT.rglob("*.py")
        if any(imported.split(".", maxsplit=1)[0] not in allowed_roots for imported in _absolute_imports(path))
    }
    assert offenders == {}


def test_plugin_entry_explicitly_builds_exactly_eight_stable_fact_handlers() -> None:
    handlers = build_handlers(
        executions=FakeExecutionReader(execution_snapshot()),
        positions=FakePositionReader(()),
        epochs=FakeEpochReader(epoch_snapshot()),
    )
    metadata = [handler.__wes_handler__ for handler in handlers]

    assert PLUGIN_KEY == "rough_sorter"
    assert PLUGIN_VERSION == "1.0.0"
    assert len(handlers) == 8
    assert all(type(item) is HandlerMetadata for item in metadata)
    assert {item.fact_type for item in metadata} == {
        MaterialEvidenceReadyFact,
        AdmissionDecidedFact,
        DevicePositionConfirmedFact,
        TargetDecidedFact,
        PlacementCompletedFact,
        ReplacementPlanDecidedFact,
        TransportOutcomePublishedFact,
        RecoveryDecidedFact,
    }
    assert all(issubclass(item.fact_type, FactReference) for item in metadata)
    assert all(item.supported_versions == ("1.0",) for item in metadata)


def test_plugin_entry_has_no_registry_discovery_or_numbered_process_names() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE_ROOT.rglob("*.py"))

    assert "registry" not in source.lower()
    assert "discover" not in source.lower()
    assert re.search(r"Phase[0-9]+", source) is None
