"""QUERY shadow/readiness 的长期能力与数据边界。"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHADOW_SOURCE = ROOT / "src/app/runtime/system_capabilities"


def _tree(name: str) -> ast.Module:
    return ast.parse((SHADOW_SOURCE / name).read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_pure_shadow_evaluator_has_no_io_database_queue_or_runtime_effect_capability() -> None:
    tree = _tree("shadow_readiness.py")
    imports = _imports(tree)

    assert not any(
        name.startswith(prefix)
        for name in imports
        for prefix in (
            "asyncio",
            "httpx",
            "sqlalchemy",
            "src.core.task_queue_gateway",
            "src.database",
            "src.app.runtime.orchestration.runtime_intent",
        )
    )


def test_shadow_path_uses_named_queue_and_never_unmanaged_create_task() -> None:
    files = (
        SHADOW_SOURCE / "shadow_readiness.py",
        SHADOW_SOURCE / "shadow_repository.py",
        SHADOW_SOURCE / "shadow_service.py",
        ROOT / "src/core/task_queue_gateway.py",
        ROOT / "src/celery_app/tasks/workline.py",
    )
    shadow_text = "\n".join(path.read_text(encoding="utf-8") for path in files)

    assert "process_query_shadow_comparison" in shadow_text
    assert "create_task(" not in shadow_text


def test_comparison_storage_is_reference_only_and_does_not_implement_first_migration() -> None:
    model_text = (SHADOW_SOURCE / "shadow_models.py").read_text(encoding="utf-8")
    source_text = "\n".join(
        (SHADOW_SOURCE / name).read_text(encoding="utf-8")
        for name in (
            "shadow_models.py",
            "shadow_readiness.py",
            "shadow_repository.py",
            "shadow_service.py",
        )
    )

    assert all(name in model_text for name in ("evidence_ref", "input_hash", "output_hash"))
    assert "replay_ref" not in model_text
    assert not any(
        forbidden in model_text for forbidden in ("request_payload", "response_payload", "authority_snapshot")
    )
    assert "rough_sorter" not in source_text
    assert "RuntimeIntent" not in source_text
    assert "SystemOutbox" not in source_text
