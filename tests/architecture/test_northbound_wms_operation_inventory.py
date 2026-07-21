"""北向 WMS operation 遗留消费者清点守护。"""

from __future__ import annotations

import ast
import csv
import re
import sys
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO_ROOT / "docs" / "architecture" / "northbound-wms-operation-inventory.csv"
ADR_PATH = REPO_ROOT / "docs" / "architecture" / "adr" / "2026-07-21-wms-operation-identity.md"
GUARD_PATH = Path(__file__).resolve()

LEGACY_IDENTITY_TARGETS = {
    "WmsInventoryQueryPort.query_inventory": "wms.inventory.query_inventory@v1",
    "wms.rough_sorter_inventory_admission@v1": "wms.inventory.query_inventory@v1",
    "WmsInventoryTransactionPort.confirm_inbound": "wms.inventory.confirm_inbound@v1",
    "WmsFulfillmentPort.notify_pkg_binding": "wms.fulfillment.notify_pkg_binding@v1",
    "WmsFulfillmentPort.full_box_exchange": "wms.fulfillment.full_box_exchange@v1",
}
TEST_OPERATION_TARGETS = {
    "query_inventory": "wms.inventory.query_inventory@v1",
    "confirm_inbound": "wms.inventory.confirm_inbound@v1",
    "notify_pkg_binding": "wms.fulfillment.notify_pkg_binding@v1",
    "full_box_exchange": "wms.fulfillment.full_box_exchange@v1",
}
REQUIRED_CATEGORIES = {
    "caller",
    "binding",
    "generated_index",
    "test",
    "metric",
    "documentation",
}
VALID_DISPOSITIONS = {"KEEP", "REWRITE", "DELETE"}

SOURCE_PATHS = (
    "src/app/runtime/capabilities/material_flow",
    "src/app/runtime/system_capabilities",
    "src/app/runtime/workline_plugins/rough_sorter",
    "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py",
    "src/app/contracts/external_contract_profile_catalog.py",
)
TEST_PATHS = ("tests",)
DOCUMENTATION_PATHS = ("docs/architecture", "docs/contracts")
METRIC_OWNER_PATHS = (
    "src/app/runtime/orchestration/observability.py",
    "src/app/runtime/orchestration/services",
    "src/app/wms_integration",
    "src/app/callback/services",
    "src/app/device/services",
    "src/celery_app/tasks",
)
STRUCTURED_OPERATION_LABELS = ("operation", "operation_identity", "operation_name", "operation_kind")
IDENTITY_SYNTAX_CHARS = r"A-Za-z0-9_.@:-"
VERSIONED_CAPABILITY_MODULES = {
    "wms.rough_sorter_inventory_admission@v1": (
        "src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission"
    )
}


def _read_inventory() -> list[dict[str, str]]:
    with INVENTORY_PATH.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _iter_python_files(relative_paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative_path in relative_paths:
        path = REPO_ROOT / relative_path
        files.extend(path.rglob("*.py") if path.is_dir() else (path,))
    return sorted(set(files))


def _category_for_source(relative_path: str) -> str:
    if relative_path == "src/app/runtime/system_capabilities/generated_index.py":
        return "generated_index"
    if relative_path in {
        "src/app/contracts/external_contract_profile_catalog.py",
        "src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/definition.py",
        "src/app/runtime/workline_plugins/rough_sorter/definition.py",
    }:
        return "binding"
    return "caller"


def _contains_exact_identity(text: str, identity: str) -> bool:
    return (
        re.search(rf"(?<![{IDENTITY_SYNTAX_CHARS}]){re.escape(identity)}(?![{IDENTITY_SYNTAX_CHARS}])", text)
        is not None
    )


def _constant_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _node_contains_version_pair(node: ast.AST, capability_key: str, contract_version: str) -> bool:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_constant_string(item) for item in node.elts]
        if (capability_key, contract_version) in pairwise(values):
            return True
    if isinstance(node, ast.Dict):
        fields = {
            _constant_string(key): _constant_string(value) for key, value in zip(node.keys, node.values, strict=True)
        }
        if fields.get("capability_key") == capability_key and fields.get("contract_version") == contract_version:
            return True
    if isinstance(node, ast.Call):
        fields = {keyword.arg: _constant_string(keyword.value) for keyword in node.keywords if keyword.arg is not None}
        if fields.get("capability_key") == capability_key and fields.get("contract_version") == contract_version:
            return True
    return False


def _contains_versioned_identity(text: str, identity: str) -> bool:
    if _contains_exact_identity(text, identity):
        return True
    capability_key, separator, contract_version = identity.rpartition("@")
    if not separator:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(_node_contains_version_pair(node, capability_key, contract_version) for node in ast.walk(tree))


def _is_package_or_submodule(imported_module: str, package_module: str) -> bool:
    return imported_module == package_module or imported_module.startswith(f"{package_module}.")


def _contains_legacy_identity(text: str, identity: str, relative_path: str) -> bool:
    if identity == "wms.rough_sorter_inventory_admission@v1":
        if _contains_versioned_identity(text, identity):
            return True
        module_path = VERSIONED_CAPABILITY_MODULES[identity]
        if relative_path.startswith(f"{module_path.replace('.', '/')}/"):
            return True
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return False
        return any(
            (isinstance(node, ast.ImportFrom) and _is_package_or_submodule(node.module or "", module_path))
            or (
                isinstance(node, ast.Import)
                and any(_is_package_or_submodule(imported.name, module_path) for imported in node.names)
            )
            for node in ast.walk(tree)
        )
    return _contains_exact_identity(text, identity)


def _looks_like_wms_contract_name(name: str) -> bool:
    normalized = name.lstrip("_")
    if normalized.startswith("Wms"):
        return True
    port_suffixes = {
        legacy_identity.split(".", 1)[0].removeprefix("Wms")
        for legacy_identity in LEGACY_IDENTITY_TARGETS
        if legacy_identity.startswith("Wms")
    }
    return any(normalized.endswith(port_suffix) for port_suffix in port_suffixes)


def _expression_references_wms_contract(node: ast.AST | None, contract_names: set[str]) -> bool:
    if node is None:
        return False
    return any(
        (isinstance(child, ast.Name) and child.id in contract_names)
        or (isinstance(child, ast.Attribute) and child.attr in contract_names)
        or (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and (child.value == "WMS" or child.value.startswith(("WMS_", "wms.")))
        )
        for child in ast.walk(node)
    )


def _assignment_target_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _wms_contract_names(tree: ast.AST) -> set[str]:
    contract_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if _looks_like_wms_contract_name(alias.name.rsplit(".", 1)[-1])
    }
    contract_names.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _looks_like_wms_contract_name(node.name)
    )
    contract_names.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _expression_references_wms_contract(node.returns, contract_names)
    )

    changed = True
    while changed:
        changed = False
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if _call_path(call).rsplit(".", 1)[-1] != "register" or len(call.args) < 2:
                continue
            if not _expression_references_wms_contract(call.args[0], contract_names):
                continue
            registered_names = {node.id for node in ast.walk(call.args[1]) if isinstance(node, ast.Name)}
            if new_names := registered_names - contract_names:
                contract_names.update(new_names)
                changed = True

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            if not (
                _expression_references_wms_contract(node.value, contract_names)
                or (
                    isinstance(node, ast.AnnAssign)
                    and _expression_references_wms_contract(node.annotation, contract_names)
                )
            ):
                continue
            if new_names := _assignment_target_names(node) - contract_names:
                contract_names.update(new_names)
                changed = True
    return contract_names


def _operation_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and child.value in TEST_OPERATION_TARGETS
    }


def _loop_contract_operations(node: ast.For, contract_names: set[str], bindings: dict[str, set[str]]) -> set[str]:
    if not isinstance(node.target, ast.Name):
        return set()
    if isinstance(node.iter, ast.Name):
        operations = bindings.get(node.iter.id, set())
    else:
        operations = _operation_constants(node.iter)
    if not operations:
        return set()
    for call in (child for child in ast.walk(node) if isinstance(child, ast.Call) and len(child.args) >= 2):
        if _call_path(call).rsplit(".", 1)[-1] not in {"getattr", "hasattr", "setattr"}:
            continue
        if not _expression_references_wms_contract(call.args[0], contract_names):
            continue
        if isinstance(call.args[1], ast.Name) and call.args[1].id == node.target.id:
            return operations
    return set()


def _discover_test_operation_names(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()

    contract_names = _wms_contract_names(tree)
    discovered: set[str] = set()

    for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        if class_node.name not in contract_names:
            continue
        discovered.update(
            method.name
            for method in class_node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in TEST_OPERATION_TARGETS
        )

    for attribute in (node for node in ast.walk(tree) if isinstance(node, ast.Attribute)):
        if attribute.attr in TEST_OPERATION_TARGETS and _expression_references_wms_contract(
            attribute.value, contract_names
        ):
            discovered.add(attribute.attr)

    bindings = {
        target_name: _operation_constants(node.value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
        for target_name in _assignment_target_names(node)
        if _operation_constants(node.value)
    }
    for loop in (node for node in ast.walk(tree) if isinstance(node, ast.For)):
        discovered.update(_loop_contract_operations(loop, contract_names, bindings))

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        call_name = _call_path(call).rsplit(".", 1)[-1]
        if (
            call_name in {"getattr", "hasattr", "setattr"}
            and len(call.args) >= 2
            and _expression_references_wms_contract(call.args[0], contract_names)
        ):
            operation_name = _constant_string(call.args[1])
            if operation_name in TEST_OPERATION_TARGETS:
                discovered.add(operation_name)
        if _expression_references_wms_contract(call, contract_names):
            discovered.update(_operation_constants(call))
    return discovered


def _without_full_identities(text: str) -> str:
    full_identities = (*LEGACY_IDENTITY_TARGETS, *LEGACY_IDENTITY_TARGETS.values())
    for identity in sorted(set(full_identities), key=len, reverse=True):
        text = re.sub(
            rf"(?<![{IDENTITY_SYNTAX_CHARS}]){re.escape(identity)}(?![{IDENTITY_SYNTAX_CHARS}])",
            "",
            text,
        )
    return text


def _discover_split_port_method_references(text: str) -> set[tuple[str, str]]:
    discovered: set[tuple[str, str]] = set()
    port_methods = {
        tuple(legacy_identity.rsplit(".", 1)): target_identity
        for legacy_identity, target_identity in LEGACY_IDENTITY_TARGETS.items()
        if legacy_identity.startswith("Wms") and "." in legacy_identity
    }
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        code_identities = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line))
        for (port_name, method_name), target_identity in port_methods.items():
            if {port_name, method_name} <= code_identities:
                discovered.add((f"{port_name}.{method_name}", target_identity))
    return discovered


def _discover_references() -> set[tuple[str, str, str, str]]:
    discovered: set[tuple[str, str, str, str]] = set()
    path_groups = (
        (SOURCE_PATHS, None),
        (TEST_PATHS, "test"),
        (DOCUMENTATION_PATHS, "documentation"),
    )
    excluded = {INVENTORY_PATH, ADR_PATH, GUARD_PATH}
    for paths, fixed_category in path_groups:
        for path in _iter_python_files(paths) if fixed_category != "documentation" else _iter_markdown_files(paths):
            if path in excluded:
                continue
            text = path.read_text(encoding="utf-8")
            relative_path = path.relative_to(REPO_ROOT).as_posix()
            category = fixed_category or _category_for_source(relative_path)
            for legacy_identity, target_identity in LEGACY_IDENTITY_TARGETS.items():
                if _contains_legacy_identity(text, legacy_identity, relative_path):
                    discovered.add((category, relative_path, legacy_identity, target_identity))
            if category == "documentation":
                for legacy_identity, target_identity in _discover_split_port_method_references(text):
                    discovered.add((category, relative_path, legacy_identity, target_identity))
            if category == "test":
                residual_text = _without_full_identities(text)
                for operation_name in _discover_test_operation_names(residual_text):
                    discovered.add((category, relative_path, operation_name, TEST_OPERATION_TARGETS[operation_name]))
    return discovered


def _call_path(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.AST = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_metric_call(call: ast.Call) -> bool:
    call_path = _call_path(call)
    leaf_name = call_path.rsplit(".", 1)[-1]
    return leaf_name in {"RuntimeObservabilitySignal", "emit_metric", "observability_emit", "_observability_emit"} or (
        leaf_name == "emit" and "observability" in call_path
    )


def _metric_label_nodes(call: ast.Call) -> list[ast.AST]:
    label_nodes: list[ast.AST] = []
    for node in ast.walk(call):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if _constant_string(key) in STRUCTURED_OPERATION_LABELS:
                    label_nodes.append(value)
        elif isinstance(node, ast.keyword) and node.arg in STRUCTURED_OPERATION_LABELS:
            label_nodes.append(node.value)
    return label_nodes


def _function_call_graph(functions: list[ast.FunctionDef | ast.AsyncFunctionDef]) -> dict[str, set[str]]:
    return {
        function.name: {
            _call_path(call).rsplit(".", 1)[-1] for call in ast.walk(function) if isinstance(call, ast.Call)
        }
        for function in functions
    }


def _function_reaches(graph: dict[str, set[str]], source: str, target: str) -> bool:
    pending = [source]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, set()) - visited)
    return False


def _method_parameters(method: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    parameters = [*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs]
    return [parameter.arg for parameter in parameters if parameter.arg not in {"self", "cls"}]


def _expression_uses_name(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(child, ast.Name) and child.id in names for child in ast.walk(node))


def _tainted_local_names(method: ast.FunctionDef | ast.AsyncFunctionDef, initial_names: set[str]) -> set[str]:
    tainted = set(initial_names)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(method):
            if isinstance(node, ast.Assign) and _expression_uses_name(node.value, tainted):
                targets = [target for target in node.targets if isinstance(target, ast.Name)]
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and _expression_uses_name(node.value, tainted)
            ):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if target.id not in tainted:
                    tainted.add(target.id)
                    changed = True
    return tainted


def _resolved_endpoint_names(execute_method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    resolved: set[str] = set()
    for node in ast.walk(execute_method):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        if not isinstance(node.value, ast.Call) or _call_path(node.value).rsplit(".", 1)[-1] != "resolve":
            continue
        if not any(isinstance(argument, ast.Name) and argument.id == "operation_name" for argument in node.value.args):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        resolved.update(target.id for target in targets if isinstance(target, ast.Name))
    return resolved


def _class_has_execute_endpoint_metric_flow(methods: list[ast.FunctionDef | ast.AsyncFunctionDef]) -> bool:
    methods_by_name = {method.name: method for method in methods}
    execute_method = methods_by_name.get("_execute")
    if execute_method is None:
        return False
    endpoint_names = _resolved_endpoint_names(execute_method)
    if not endpoint_names:
        return False

    incoming_taint = {"_execute": endpoint_names}
    pending = ["_execute"]
    while pending:
        method_name = pending.pop()
        method = methods_by_name[method_name]
        local_taint = _tainted_local_names(method, incoming_taint[method_name])
        for call in (node for node in ast.walk(method) if isinstance(node, ast.Call)):
            if _is_metric_call(call) and any(
                isinstance(label_node, ast.Attribute)
                and label_node.attr == "operation_name"
                and isinstance(label_node.value, ast.Name)
                and label_node.value.id in local_taint
                for label_node in _metric_label_nodes(call)
            ):
                return True

            called_method_name = _call_path(call).rsplit(".", 1)[-1]
            called_method = methods_by_name.get(called_method_name)
            if called_method is None:
                continue
            parameters = _method_parameters(called_method)
            passed_taint = {
                parameter
                for parameter, argument in zip(parameters, call.args, strict=False)
                if _expression_uses_name(argument, local_taint)
            }
            passed_taint.update(
                keyword.arg
                for keyword in call.keywords
                if keyword.arg in parameters and _expression_uses_name(keyword.value, local_taint)
            )
            previous_taint = incoming_taint.setdefault(called_method_name, set())
            new_taint = passed_taint - previous_taint
            if new_taint:
                previous_taint.update(new_taint)
                pending.append(called_method_name)
    return False


def _dynamic_metric_operations(tree: ast.AST) -> set[str]:
    discovered: set[str] = set()
    for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        methods = [node for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        graph = _function_call_graph(methods)
        metric_methods = {
            method.name
            for method in methods
            if any(
                isinstance(label_node, ast.Attribute) and label_node.attr == "operation_name"
                for call in ast.walk(method)
                if isinstance(call, ast.Call) and _is_metric_call(call)
                for label_node in _metric_label_nodes(call)
            )
        }
        if not any(_function_reaches(graph, "_execute", method_name) for method_name in metric_methods):
            continue
        if not _class_has_execute_endpoint_metric_flow(methods):
            continue
        discovered.update(
            operation_name
            for method in methods
            for call in ast.walk(method)
            if isinstance(call, ast.Call) and _call_path(call).rsplit(".", 1)[-1] == "_execute" and call.args
            if (operation_name := _constant_string(call.args[0])) in TEST_OPERATION_TARGETS
        )
    return discovered


def _discover_metric_references() -> set[tuple[str, str, str, str]]:
    discovered: set[tuple[str, str, str, str]] = set()
    for path in _iter_python_files(METRIC_OWNER_PATHS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_metric_call(node)):
            for label_node in _metric_label_nodes(call):
                label_value = _constant_string(label_node)
                if label_value in LEGACY_IDENTITY_TARGETS:
                    discovered.add(("metric", relative_path, label_value, LEGACY_IDENTITY_TARGETS[label_value]))
                elif label_value in LEGACY_IDENTITY_TARGETS.values():
                    discovered.add(("metric", relative_path, label_value, label_value))
                elif label_value in TEST_OPERATION_TARGETS:
                    discovered.add(("metric", relative_path, label_value, TEST_OPERATION_TARGETS[label_value]))
        for operation_name in _dynamic_metric_operations(tree):
            discovered.add(("metric", relative_path, operation_name, TEST_OPERATION_TARGETS[operation_name]))
    return discovered


def _iter_markdown_files(relative_paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative_path in relative_paths:
        files.extend((REPO_ROOT / relative_path).rglob("*.md"))
    return sorted(set(files))


def test_reference_scanner_preserves_legacy_identity_and_parses_split_port_method_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Port 与方法分列时仍按完整 legacy identity 清点，且不能命中词片段。"""
    document_path = tmp_path / "docs" / "architecture" / "port-table.md"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(
        """| Port | 关键方法 |
| --- | --- |
| `WmsInventoryQueryPort` | `query_inventory` / `query_inventory_cache` |
| `WmsFulfillmentPort` | `full_box_exchange` / `notify_pkg_binding` |
| `WmsInventoryQueryPortability` | `query_inventory_preview` |
""",
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_PATHS", ())
    monkeypatch.setattr(module, "TEST_PATHS", ())
    monkeypatch.setattr(module, "DOCUMENTATION_PATHS", ("docs/architecture",))

    assert _discover_references() == {
        (
            "documentation",
            "docs/architecture/port-table.md",
            "WmsInventoryQueryPort.query_inventory",
            "wms.inventory.query_inventory@v1",
        ),
        (
            "documentation",
            "docs/architecture/port-table.md",
            "WmsFulfillmentPort.full_box_exchange",
            "wms.fulfillment.full_box_exchange@v1",
        ),
        (
            "documentation",
            "docs/architecture/port-table.md",
            "WmsFulfillmentPort.notify_pkg_binding",
            "wms.fulfillment.notify_pkg_binding@v1",
        ),
    }


def test_reference_scanner_requires_full_versioned_identity_and_syntax_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capability identity 必须包含版本，且不能命中任何 identity 扩展串。"""
    source_root = tmp_path / "src" / "app" / "runtime" / "system_capabilities"
    source_root.mkdir(parents=True)
    (source_root / "identities.py").write_text(
        'ACTIVE = ("wms.rough_sorter_inventory_admission", "v1")\n'
        'METHOD_EXTENSION = "WmsInventoryQueryPort.query_inventory.preview"\n',
        encoding="utf-8",
    )
    (source_root / "extensions.py").write_text(
        'WRONG_VERSION = "wms.rough_sorter_inventory_admission@v2"\n'
        'DOTTED_EXTENSION = "wms.rough_sorter_inventory_admission@v1.preview"\n'
        'DASHED_EXTENSION = "wms.rough_sorter_inventory_admission@v1-preview"\n',
        encoding="utf-8",
    )
    (source_root / "consumer.py").write_text(
        "from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission import DEFINITION\n",
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_PATHS", ("src/app/runtime/system_capabilities",))
    monkeypatch.setattr(module, "TEST_PATHS", ())
    monkeypatch.setattr(module, "DOCUMENTATION_PATHS", ())

    assert "wms.rough_sorter_inventory_admission@v1" in LEGACY_IDENTITY_TARGETS
    assert "wms.rough_sorter_inventory_admission" not in LEGACY_IDENTITY_TARGETS
    assert _discover_references() == {
        (
            "caller",
            "src/app/runtime/system_capabilities/identities.py",
            "wms.rough_sorter_inventory_admission@v1",
            "wms.inventory.query_inventory@v1",
        ),
        (
            "caller",
            "src/app/runtime/system_capabilities/consumer.py",
            "wms.rough_sorter_inventory_admission@v1",
            "wms.inventory.query_inventory@v1",
        ),
    }
    for extension in ("@v2", ".preview", "-preview", ":preview", "_preview"):
        assert not _contains_exact_identity(
            f"wms.inventory.query_inventory@v1{extension}", "wms.inventory.query_inventory@v1"
        )


def test_reference_scanner_distinguishes_capability_package_from_preview_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """版本化 capability 只归属精确 package 及其子模块，不归属同前缀兄弟模块。"""
    source_root = tmp_path / "src" / "app" / "runtime" / "system_capabilities"
    source_root.mkdir(parents=True)
    (source_root / "real_consumer.py").write_text(
        "from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission import DEFINITION\n"
        "import src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.handler\n",
        encoding="utf-8",
    )
    (source_root / "preview_consumer.py").write_text(
        "from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission_preview import PREVIEW\n"
        "import src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission_preview.handler\n",
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_PATHS", ("src/app/runtime/system_capabilities",))
    monkeypatch.setattr(module, "TEST_PATHS", ())
    monkeypatch.setattr(module, "DOCUMENTATION_PATHS", ())

    assert _discover_references() == {
        (
            "caller",
            "src/app/runtime/system_capabilities/real_consumer.py",
            "wms.rough_sorter_inventory_admission@v1",
            "wms.inventory.query_inventory@v1",
        )
    }


def test_test_scanner_rejects_bare_operation_names_without_wms_contract_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 WMS 方法、同名 helper/参数与无关字符串不得进入 operation 清点。"""
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (test_root / "test_unrelated_operations.py").write_text(
        """from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort


class AnalyticsClient:
    def query_inventory(self):
        return None


def confirm_inbound():
    return None


def passthrough(notify_pkg_binding):
    return notify_pkg_binding


analytics = AnalyticsClient()
analytics.query_inventory()
confirm_inbound()
UNRELATED_EVENT = "full_box_exchange"
""",
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_PATHS", ())
    monkeypatch.setattr(module, "TEST_PATHS", ("tests",))
    monkeypatch.setattr(module, "DOCUMENTATION_PATHS", ())

    assert _discover_references() == set()


def test_metric_scanner_recognizes_all_supported_operation_identity_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指标 owner 中 legacy、目标 identity 与结构化 label 都必须进入双向清点。"""
    metric_root = tmp_path / "src" / "app" / "runtime" / "orchestration"
    metric_root.mkdir(parents=True)
    (metric_root / "legacy_metric.py").write_text(
        'emit_metric("wms.operation", {"operation": "WmsInventoryQueryPort.query_inventory"})\n',
        encoding="utf-8",
    )
    (metric_root / "target_metric.py").write_text(
        'emit_metric("wms.operation", {"operation_identity": "wms.inventory.confirm_inbound@v1"})\n',
        encoding="utf-8",
    )
    (metric_root / "label_metric.py").write_text(
        'emit_metric("wms.operation", {"operation_name": "notify_pkg_binding"})\n'
        'emit_metric("wms.operation.preview", {"operation_name": "notify_pkg_binding_preview"})\n',
        encoding="utf-8",
    )
    (metric_root / "dynamic_metric.py").write_text(
        """class DynamicMetric:
    async def query_inventory(self, request):
        return await self._execute("query_inventory", request)

    async def confirm_inbound(self, request):
        return await self._execute("confirm_inbound", request)

    async def _execute(self, operation_name, request):
        endpoint = self.resolve(operation_name)
        self._emit_failure(endpoint)

    def _emit_failure(self, endpoint):
        emit_metric("wms.failure", {"operation_kind": endpoint.operation_name})


class UnrelatedOperation:
    async def full_box_exchange(self, request):
        return await self._execute("full_box_exchange", request)

    async def _execute(self, operation_name, request):
        return operation_name, request
""",
        encoding="utf-8",
    )
    (metric_root / "unrelated_metric.py").write_text(
        'emit_metric("runtime.unrelated", {"result": "failed"})\n'
        'UNRELATED_LABEL = {"operation_name": "full_box_exchange"}\n'
        'UNRELATED_OPERATION = "WmsFulfillmentPort.full_box_exchange"\n',
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "METRIC_OWNER_PATHS", ("src/app/runtime/orchestration",))

    assert _discover_metric_references() == {
        (
            "metric",
            "src/app/runtime/orchestration/legacy_metric.py",
            "WmsInventoryQueryPort.query_inventory",
            "wms.inventory.query_inventory@v1",
        ),
        (
            "metric",
            "src/app/runtime/orchestration/target_metric.py",
            "wms.inventory.confirm_inbound@v1",
            "wms.inventory.confirm_inbound@v1",
        ),
        (
            "metric",
            "src/app/runtime/orchestration/label_metric.py",
            "notify_pkg_binding",
            "wms.fulfillment.notify_pkg_binding@v1",
        ),
        (
            "metric",
            "src/app/runtime/orchestration/dynamic_metric.py",
            "confirm_inbound",
            "wms.inventory.confirm_inbound@v1",
        ),
        (
            "metric",
            "src/app/runtime/orchestration/dynamic_metric.py",
            "query_inventory",
            "wms.inventory.query_inventory@v1",
        ),
    }


def test_metric_scanner_rejects_reachable_operation_name_from_unrelated_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无关对象的 operation_name 不得归属给 _execute operation。"""
    metric_root = tmp_path / "src" / "app" / "runtime" / "orchestration"
    metric_root.mkdir(parents=True)
    (metric_root / "unrelated_dynamic_metric.py").write_text(
        """class DynamicMetric:
    async def query_inventory(self, request):
        return await self._execute("query_inventory", request)

    async def _execute(self, operation_name, request):
        endpoint = self.resolve(operation_name)
        self._emit_failure(endpoint, self.unrelated_context)

    def _emit_failure(self, endpoint, unrelated_context):
        emit_metric("wms.failure", {"operation_kind": unrelated_context.operation_name})
""",
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "METRIC_OWNER_PATHS", ("src/app/runtime/orchestration",))

    assert _discover_metric_references() == set()


def test_inventory_covers_every_discovered_legacy_reference() -> None:
    """真实扫描结果与清点表必须双向对应，禁止漏项或无来源静态罗列。"""
    rows = _read_inventory()
    inventoried = {
        (row["category"], row["source_path"], row["legacy_identity"], row["target_operation_identity"])
        for row in rows
        if row["category"] != "metric" and not row["source_path"].startswith("<missing:")
    }
    discovered = _discover_references()

    assert discovered == inventoried, (
        f"清点表与真实遗留引用不一致；漏项={sorted(discovered - inventoried)}；"
        f"无来源项={sorted(inventoried - discovered)}"
    )


def test_inventory_has_complete_categories_identities_and_dispositions() -> None:
    """六类条目、目标 identity、owner 与删除门禁必须完整。"""
    rows = _read_inventory()
    assert {row["category"] for row in rows} == REQUIRED_CATEGORIES
    assert {row["target_operation_identity"] for row in rows} == set(LEGACY_IDENTITY_TARGETS.values())
    assert all(row["disposition"] in VALID_DISPOSITIONS for row in rows)
    assert all(row["owner"] and row["removal_gate"] for row in rows)
    assert len({row["entry_id"] for row in rows}) == len(rows)

    test_rows = [row for row in rows if row["category"] == "test"]
    assert test_rows
    assert all(row["disposition"] in VALID_DISPOSITIONS for row in test_rows)


def test_missing_operation_metrics_are_explicit_inventory_gaps() -> None:
    """真实 operation 指标与清点表按来源双向对应，其余 operation 显式登记缺口。"""
    metric_rows = [row for row in _read_inventory() if row["category"] == "metric"]
    real_rows = {
        (row["category"], row["source_path"], row["legacy_identity"], row["target_operation_identity"])
        for row in metric_rows
        if not row["source_path"].startswith("<missing:")
    }
    discovered = _discover_metric_references()
    assert discovered == real_rows, (
        f"指标清点表与真实 operation 指标不一致；漏项={sorted(discovered - real_rows)}；"
        f"无来源项={sorted(real_rows - discovered)}"
    )

    discovered_targets = {target_identity for _, _, _, target_identity in discovered}
    missing_rows = [row for row in metric_rows if row["source_path"] == "<missing:operation_metric>"]
    assert {row["target_operation_identity"] for row in missing_rows} == (
        set(LEGACY_IDENTITY_TARGETS.values()) - discovered_targets
    )
    assert all(row["legacy_identity"] == "<absent>" for row in missing_rows)
    assert all(row["disposition"] == "REWRITE" for row in metric_rows)


def test_adr_and_inventory_share_the_same_stable_operation_identities() -> None:
    """ADR 决策出的稳定 identity 必须与清点表目标集合完全一致。"""
    inventory_identities = {row["target_operation_identity"] for row in _read_inventory()}
    adr_text = ADR_PATH.read_text(encoding="utf-8")
    adr_identities = set(re.findall(r"^\| `([^`]+@v1)` \|", adr_text, flags=re.MULTILINE))

    assert adr_identities == inventory_identities
    assert "本次清点锁定四个真实 operation" in adr_text
    assert "四个真实消费者" not in adr_text
    for required_boundary in ("typed contract", "catalog", "Provider", "删除门禁", "不预建空壳", "不保留兼容"):
        assert required_boundary in adr_text
