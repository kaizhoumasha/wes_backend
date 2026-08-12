"""WMS EFFECT 共享执行管线的物理结构门禁。"""

from __future__ import annotations

import ast
from pathlib import Path

from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_ROOT = REPO_ROOT / "src/app/runtime/system_capabilities/wms"


def test_each_effect_definition_is_only_a_thin_builder_composition() -> None:
    definition_paths = []
    for operation in EFFECT_OPERATIONS:
        capability_key, _version = operation.identity.rsplit("@", maxsplit=1)
        domain, action = capability_key.removeprefix("wms.").split(".", maxsplit=1)
        definition_paths.append(CAPABILITY_ROOT / domain / action / "definition.py")

    assert len(definition_paths) == 11
    assert all(path.is_file() for path in definition_paths)
    for path in definition_paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "build_wms_effect_capability_definition" in source
        assert not any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)
        )


def test_effect_runtime_does_not_create_a_second_http_sender_or_branch_on_operation_identity() -> None:
    runtime_path = CAPABILITY_ROOT / "effect_runtime.py"
    runtime_source = runtime_path.read_text(encoding="utf-8")
    outbox_engine_source = (REPO_ROOT / "src/app/sys/services/outbox_engine.py").read_text(encoding="utf-8")

    assert "httpx" not in runtime_source
    assert "AsyncClient" not in runtime_source
    assert "operation.identity ==" not in runtime_source
    assert outbox_engine_source.count("async def _send_external_http(") == 1


def test_sync_terminal_events_have_no_async_status_or_callback_producer() -> None:
    bridge_source = (REPO_ROOT / "src/app/runtime/orchestration/effect_bridges.py").read_text(encoding="utf-8")
    status_source = (REPO_ROOT / "src/app/runtime/orchestration/services/wms_effect_status_service.py").read_text(
        encoding="utf-8"
    )
    bridge_tree = ast.parse(bridge_source)
    sync_resolution = next(
        node
        for node in bridge_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_sync_transport_resolution"
    )
    callback_class = next(
        node for node in bridge_tree.body if isinstance(node, ast.ClassDef) and node.name == "EffectCallbackBridge"
    )
    sync_resolution_source = ast.get_source_segment(bridge_source, sync_resolution)
    callback_source = ast.get_source_segment(bridge_source, callback_class)

    assert sync_resolution_source is not None
    assert sync_resolution_source.count("EffectReducerEventType.SYNC_COMPLETED") == 1
    assert sync_resolution_source.count("EffectReducerEventType.SYNC_REJECTED") == 1
    assert "SYNC_COMPLETED" not in status_source
    assert "SYNC_REJECTED" not in status_source
    assert callback_source is not None
    assert "SYNC_COMPLETED" not in callback_source
    assert "SYNC_REJECTED" not in callback_source


def test_shared_sync_interpreter_delegates_identity_without_operation_type_switches() -> None:
    runtime_path = REPO_ROOT / "src/app/wms_integration/effect_runtime.py"
    source = runtime_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    validator = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "validate_effect_terminal_result"
    )

    assert not any(isinstance(node, ast.Match) for node in ast.walk(validator))
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"isinstance", "type"}
        for node in ast.walk(validator)
    )
    assert "operation.identity" not in (ast.get_source_segment(source, validator) or "")
