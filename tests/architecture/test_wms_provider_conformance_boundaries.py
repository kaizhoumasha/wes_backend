"""WMS Provider conformance/replay/simulator 的能力边界门禁。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src.app.runtime.system_capabilities.wms import provider_conformance
from tests.mock import wms_scripted_provider
from tests.support.wms_provider_conformance import QueryInventoryReplayFactory

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pure_runner_has_no_network_credential_or_persistence_import_capability() -> None:
    tree = ast.parse(inspect.getsource(provider_conformance))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", maxsplit=1)[0])

    assert imported_roots.isdisjoint({"httpx", "os", "socket", "sqlalchemy", "urllib", "requests"})
    for function in (
        provider_conformance.build_wms_conformance_report,
        provider_conformance.verify_wms_conformance_report,
        provider_conformance.run_query_inventory_staging_live_conformance,
    ):
        parameters = set(inspect.signature(function).parameters)
        assert parameters.isdisjoint({"base_url", "endpoint", "headers", "credential", "secret", "transport"})


def test_simulator_is_test_only_in_process_and_does_not_copy_production_lifecycle() -> None:
    simulator_path = Path(wms_scripted_provider.__file__).resolve()
    source = simulator_path.read_text(encoding="utf-8")

    assert simulator_path.is_relative_to(REPO_ROOT / "tests/mock")
    assert set(wms_scripted_provider.ScriptedWmsQueryInventoryProvider.__slots__) == {"_case"}
    assert all(
        token not in source
        for token in (
            "FastAPI",
            "uvicorn",
            "AsyncClient",
            "WmsQueryTransportExecutor",
            "EnvironmentWmsCredentialProvider",
            "create_engine",
            "create_task",
        )
    )


def test_production_tree_cannot_import_or_register_the_test_simulator() -> None:
    violations: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "tests.mock.wms_scripted_provider" in source or "ScriptedWmsQueryInventoryProvider" in source:
            violations.append(path.relative_to(REPO_ROOT).as_posix())
    assert violations == []


def test_replay_factory_source_has_no_external_effect_capability() -> None:
    source = inspect.getsource(QueryInventoryReplayFactory)
    tree = ast.parse(source)

    forbidden_names = {"open", "urlopen", "getenv", "AsyncClient", "Client", "send", "post", "write"}
    called_names = {
        node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert forbidden_names.isdisjoint(called_names | called_attributes)
