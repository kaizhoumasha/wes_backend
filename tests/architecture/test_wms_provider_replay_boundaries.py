"""WMS Provider replay 独立纯 test-support 模块的能力边界。"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPLAY_SUPPORT_PATH = REPO_ROOT / "tests/support/wms_provider_replay.py"


def test_replay_support_is_an_independent_pure_module() -> None:
    assert REPLAY_SUPPORT_PATH.is_file()
    source = REPLAY_SUPPORT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_fragments = {
        "httpx",
        "credential",
        "runtime_factory",
        "query_transport",
        "wms_integration.adapters",
    }
    assert all(
        fragment not in imported_module for imported_module in imported_modules for fragment in forbidden_fragments
    )
    assert all(
        token not in source
        for token in (
            "WmsQueryTransportExecutor",
            "InventoryQueryOperationAdapter",
            "EnvironmentWmsCredentialProvider",
            "MockTransport",
        )
    )


def test_replay_models_loader_reconstruction_and_factory_are_owned_by_pure_module() -> None:
    replay_support = importlib.import_module("tests.support.wms_provider_replay")

    for name in (
        "ReplayInventoryItem",
        "QueryInventoryReplayRecord",
        "QueryInventoryReplayFixture",
        "load_query_inventory_replay_fixture",
        "reconstruct_query_inventory_outcome",
        "QueryInventoryReplayFactory",
        "verify_query_inventory_replay_report",
    ):
        symbol = getattr(replay_support, name)
        assert symbol.__module__ == replay_support.__name__

    assert set(replay_support.__all__) >= {
        "QUERY_INVENTORY_REPLAY_ASSET_DIGEST",
        "QUERY_INVENTORY_REPLAY_FIXTURE",
        "QueryInventoryReplayFactory",
        "load_query_inventory_replay_fixture",
        "reconstruct_query_inventory_outcome",
        "verify_query_inventory_replay_report",
    }
