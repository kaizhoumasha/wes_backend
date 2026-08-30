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
EXPECTED_ASYNC_CLIENT_CREATORS = frozenset({"core/outbound_http/factory.py"})
EXPECTED_WMS_CLIENT_BUILDERS = frozenset({"app/transport/composition.py"})


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


def _type_checking_nodes(tree: ast.AST) -> set[int]:
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        is_type_checking = is_type_checking or (
            isinstance(test, ast.Attribute)
            and isinstance(test.value, ast.Name)
            and test.value.id == "typing"
            and test.attr == "TYPE_CHECKING"
        )
        if is_type_checking:
            excluded.update(id(child) for statement in node.body for child in ast.walk(statement))
    return excluded


def _httpx_client_creator_paths(root: Path, client_name: str) -> set[str]:
    creator_paths: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        excluded = _type_checking_nodes(tree)
        module_aliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if id(node) not in excluded and isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "httpx"
        }
        constructor_aliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if id(node) not in excluded and isinstance(node, ast.ImportFrom) and node.module == "httpx"
            for alias in node.names
            if alias.name == client_name
        }
        if any(
            isinstance(node, ast.Call)
            and id(node) not in excluded
            and (
                (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in module_aliases
                    and node.func.attr == client_name
                )
                or (isinstance(node.func, ast.Name) and node.func.id in constructor_aliases)
            )
            for node in ast.walk(tree)
        ):
            creator_paths.add(path.relative_to(root).as_posix())
    return creator_paths


def _direct_httpx_import_paths(root: Path) -> set[str]:
    import_paths: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        excluded = _type_checking_nodes(tree)
        if any(
            id(node) not in excluded
            and (
                (isinstance(node, ast.Import) and any(alias.name == "httpx" for alias in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "httpx")
            )
            for node in ast.walk(tree)
        ):
            import_paths.add(path.relative_to(root).as_posix())
    return import_paths


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr}"
    return None


def _resolved_call_sites(root: Path, *, module_name: str, function_name: str) -> dict[str, tuple[int, ...]]:
    call_sites: dict[str, tuple[int, ...]] = {}
    expected_name = f"{module_name}.{function_name}"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        excluded = _type_checking_nodes(tree)
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if id(node) in excluded:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".", 1)[0]] = (
                        alias.name if alias.asname else alias.name.split(".", 1)[0]
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

        lines: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in excluded:
                continue
            dotted_name = _dotted_name(node.func)
            if dotted_name is None:
                continue
            first, separator, remainder = dotted_name.partition(".")
            resolved_name = aliases.get(first, first)
            if separator:
                resolved_name = f"{resolved_name}.{remainder}"
            if resolved_name == expected_name:
                lines.append(node.lineno)
        if lines:
            call_sites[path.relative_to(root).as_posix()] = tuple(sorted(lines))
    return dict(sorted(call_sites.items()))


def test_scanners_identify_constructed_boundary_violations(tmp_path: Path) -> None:
    source = tmp_path / "candidate.py"
    source.write_text(
        "import httpx as hx\n"
        "from httpx import Client as SyncClient\n"
        "from src.app.wms_integration import services\n"
        "async_client = hx.AsyncClient()\n"
        "sync_client = SyncClient()\n",
        encoding="utf-8",
    )

    assert _forbidden_imports(source) == {"src.app.wms_integration"}
    assert _httpx_client_creator_paths(tmp_path, "AsyncClient") == {"candidate.py"}
    assert _httpx_client_creator_paths(tmp_path, "Client") == {"candidate.py"}


def test_scanners_exclude_bounded_type_checking_imports_and_test_clients(tmp_path: Path) -> None:
    source = tmp_path / "types_only.py"
    source.write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import httpx\n    client: httpx.AsyncClient\n",
        encoding="utf-8",
    )

    assert _direct_httpx_import_paths(tmp_path) == set()
    assert _httpx_client_creator_paths(tmp_path, "AsyncClient") == set()


def test_call_scanner_resolves_aliases_qualified_calls_and_duplicate_counts(tmp_path: Path) -> None:
    fixtures = {
        "direct_alias.py": (
            "from src.app.wms_adapter.factory import build_wms_client as make_client\n"
            "make_client(base_url='http://wms', timeout_seconds=10)\n"
        ),
        "module_alias.py": (
            "import src.app.wms_adapter.factory as wms_factory\n"
            "wms_factory.build_wms_client(base_url='http://wms', timeout_seconds=10)\n"
            "wms_factory.build_wms_client(base_url='http://wms', timeout_seconds=10)\n"
        ),
        "qualified.py": (
            "import src.app.wms_adapter.factory\n"
            "src.app.wms_adapter.factory.build_wms_client(base_url='http://wms', timeout_seconds=10)\n"
        ),
        "local_name.py": "def build_wms_client(): pass\nbuild_wms_client()\n",
    }
    for relative_path, content in fixtures.items():
        (tmp_path / relative_path).write_text(content, encoding="utf-8")

    call_sites = _resolved_call_sites(
        tmp_path,
        module_name="src.app.wms_adapter.factory",
        function_name="build_wms_client",
    )

    assert {path: len(lines) for path, lines in call_sites.items()} == {
        "direct_alias.py": 1,
        "module_alias.py": 2,
        "qualified.py": 1,
    }


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


def test_httpx_async_client_creation_has_one_production_lifecycle_owner() -> None:
    current_creators = _httpx_client_creator_paths(SRC_ROOT, "AsyncClient")

    assert current_creators == EXPECTED_ASYNC_CLIENT_CREATORS


def test_sync_httpx_clients_are_absent_from_production_and_scripts() -> None:
    assert _httpx_client_creator_paths(SRC_ROOT, "Client") == set()
    assert _httpx_client_creator_paths(REPO_ROOT / "scripts", "Client") == set()
    assert _httpx_client_creator_paths(REPO_ROOT / "scripts", "AsyncClient") == set()


def test_business_packages_do_not_import_httpx_directly() -> None:
    assert _direct_httpx_import_paths(SRC_ROOT / "app") == set()


def test_wms_outbound_runtime_builds_at_most_one_shared_client() -> None:
    call_sites = _resolved_call_sites(
        SRC_ROOT,
        module_name="src.app.wms_adapter.factory",
        function_name="build_wms_client",
    )

    assert set(call_sites) == EXPECTED_WMS_CLIENT_BUILDERS
    assert sum(len(lines) for lines in call_sites.values()) == 1


def test_transport_composition_does_not_rebuild_legacy_endpoint_owners() -> None:
    imports = _imports(SRC_ROOT / "app/transport/composition.py")

    assert "src.app.wms_adapter.factory" in imports
    assert not any(
        imported == prefix or imported.startswith(f"{prefix}.")
        for imported in imports
        for prefix in ("src.app.sys", "src.app.wms_integration")
    )


def test_outbound_http_heavy_mapping_is_explicit_none_until_a_real_consumer_exists() -> None:
    heavy_mapping = tomllib.loads((REPO_ROOT / "docs/architecture/heavy-test-impact.toml").read_text(encoding="utf-8"))
    mappings = [
        mapping for mapping in heavy_mapping["mapping"] if mapping["source_glob"] == "src/core/outbound_http/**"
    ]

    assert mappings == [{"source_glob": "src/core/outbound_http/**", "heavy_tests": []}]
