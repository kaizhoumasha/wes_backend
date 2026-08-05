"""Outbound HTTP 基础层的职责与所有权门禁。"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from src.core import outbound_http

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "core/outbound_http"
EXPECTED_PACKAGE_FILES = frozenset({"__init__.py", "contracts.py", "factory.py", "transport.py"})
FORBIDDEN_IMPORT_PREFIXES = (
    "src.app",
    "src.database",
    "src.celery_app",
    "device_adapters",
    "workline_plugins",
)
FORBIDDEN_PUBLIC_TERMS = ("httpx", "auth", "credential", "hmac", "clock", "nonce", "registry", "fake")
LEGACY_ASYNC_CLIENT_CREATORS = frozenset(
    {
        "app/device/services/device_command_service.py",
        "app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py",
        "app/runtime/capabilities/material_flow/start_admission_service.py",
        "app/runtime/orchestration/services/device_command_gateway.py",
        "app/runtime/orchestration/services/inbox/outbox_dispatch_service.py",
        "app/sys/services/outbox_engine.py",
        "app/wms_integration/effect_lane_runtime.py",
        "app/wms_integration/query_runtime.py",
        "app/wms_integration/services/http_transport.py",
        "utils/request_parse.py",
    }
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    return imported_modules


def _forbidden_imports(path: Path) -> set[str]:
    return {
        imported_module
        for imported_module in _imports(path)
        if any(
            imported_module == prefix or imported_module.startswith(f"{prefix}.")
            for prefix in FORBIDDEN_IMPORT_PREFIXES
        )
    }


def _httpx_async_client_creator_paths(root: Path) -> set[str]:
    creator_paths: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
            and node.func.attr == "AsyncClient"
            for node in ast.walk(tree)
        ):
            creator_paths.add(path.relative_to(root).as_posix())
    return creator_paths


def test_scanners_identify_constructed_boundary_violations(tmp_path: Path) -> None:
    source = tmp_path / "candidate.py"
    source.write_text(
        "import httpx\nfrom src.app.wms_integration import services\nclient = httpx.AsyncClient()\n",
        encoding="utf-8",
    )

    assert _forbidden_imports(source) == {"src.app.wms_integration"}
    assert _httpx_async_client_creator_paths(tmp_path) == {"candidate.py"}


def test_outbound_http_package_has_exactly_the_four_frozen_production_files() -> None:
    assert {path.name for path in PACKAGE_ROOT.glob("*.py")} == EXPECTED_PACKAGE_FILES


def test_outbound_http_package_does_not_depend_on_business_or_infrastructure_owners() -> None:
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(_forbidden_imports(path))
        for path in PACKAGE_ROOT.glob("*.py")
        if _forbidden_imports(path)
    }

    assert offenders == {}
    assert "httpx" not in _imports(PACKAGE_ROOT / "contracts.py")


def test_outbound_http_public_surface_is_framework_neutral_and_has_no_speculative_extensions() -> None:
    expected_exports = {
        "OutboundHttpClosedError",
        "OutboundHttpDeliveryState",
        "OutboundHttpFailureKind",
        "OutboundHttpMethod",
        "OutboundHttpRequest",
        "OutboundHttpRequestError",
        "OutboundHttpResponseLimits",
        "OutboundHttpResult",
        "OutboundHttpTransport",
        "build_outbound_http_transport",
    }

    assert set(outbound_http.__all__) == expected_exports
    assert all(hasattr(outbound_http, name) for name in outbound_http.__all__)
    assert all(term not in name.casefold() for name in outbound_http.__all__ for term in FORBIDDEN_PUBLIC_TERMS)


def test_httpx_async_client_creation_keeps_legacy_owners_frozen() -> None:
    current_creators = _httpx_async_client_creator_paths(SRC_ROOT)

    assert current_creators - LEGACY_ASYNC_CLIENT_CREATORS == {"core/outbound_http/factory.py"}
    assert current_creators & LEGACY_ASYNC_CLIENT_CREATORS == LEGACY_ASYNC_CLIENT_CREATORS


def test_outbound_http_heavy_mapping_is_explicit_none_until_a_real_consumer_exists() -> None:
    heavy_mapping = tomllib.loads((REPO_ROOT / "docs/architecture/heavy-test-impact.toml").read_text(encoding="utf-8"))
    mappings = [
        mapping for mapping in heavy_mapping["mapping"] if mapping["source_glob"] == "src/core/outbound_http/**"
    ]

    assert mappings == [{"source_glob": "src/core/outbound_http/**", "heavy_tests": []}]
