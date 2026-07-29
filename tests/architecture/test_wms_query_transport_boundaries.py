"""WMS QUERY 通用 transport 与零兼容删除门禁。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_args, get_origin

from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
    WmsQueryOutcome,
)
from src.app.wms_integration.query_executor import WmsRegistryQueryExecutor

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_query_cache_and_legacy_exception_adapter_are_deleted_without_compatibility_layer() -> None:
    assert not (REPO_ROOT / "src/app/wms_integration/services/cache.py").exists()
    assert not (REPO_ROOT / "tests/wms_integration/test_cache.py").exists()
    assert not (REPO_ROOT / "src/app/wms_integration/adapters/inventory_query_port_adapter.py").exists()
    assert not (REPO_ROOT / "src/app/wms_integration/ports/inventory_query.py").exists()

    active_sources = (
        REPO_ROOT / "src/app/wms_integration",
        REPO_ROOT / "src/app/contracts",
        REPO_ROOT / "docs/architecture/authority-matrix.md",
        REPO_ROOT / "docs/architecture/file_index.md",
        REPO_ROOT / "docs/contracts/external-contract-profile.md",
    )
    forbidden = ("WmsQueryCacheService", "WMS_QUERY_CACHE_TTL_SECONDS", "cache_ttl_seconds")
    violations: list[str] = []
    for source_path in active_sources:
        paths = source_path.rglob("*.py") if source_path.is_dir() else (source_path,)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{token}")
    assert violations == []


def test_query_outcome_is_closed_four_branch_union() -> None:
    branches = {get_origin(branch) or branch for branch in get_args(WmsQueryOutcome.__value__)}
    assert branches == {
        QuerySuccess,
        QueryBusinessReject,
        QueryTechnicalFailure,
        QueryContractFailure,
    }


def test_query_transport_has_no_operation_switch_or_catch_all_retryable() -> None:
    source = inspect.getsource(WmsRegistryQueryExecutor)
    tree = ast.parse(source)

    assert "wms.inventory." not in source
    assert "query_inventory" not in source
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if isinstance(node.type, ast.Name) and node.type.id == "Exception":
            handler_source = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            assert "QueryTechnicalFailure" not in handler_source
            assert "retryable=True" not in handler_source


def test_operation_specific_query_port_adapter_and_old_executor_are_deleted() -> None:
    assert not (REPO_ROOT / "src/app/wms_integration/ports/query_inventory_operation.py").exists()
    assert not (REPO_ROOT / "src/app/wms_integration/adapters/query_inventory_operation_adapter.py").exists()
    assert not (REPO_ROOT / "src/app/wms_integration/services/query_transport.py").exists()
