"""Runtime Repository 不得反向依赖 Service 层。"""

from __future__ import annotations

import ast
from pathlib import Path


def test_runtime_intent_log_repository_does_not_import_service_layer() -> None:
    repository = (
        Path(__file__).resolve().parents[2]
        / "src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py"
    )
    tree = ast.parse(repository.read_text(encoding="utf-8"))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and isinstance(node.module, str)
    }
    imported_modules.update(
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    )

    assert not any(module.startswith("src.app.runtime.orchestration.services") for module in imported_modules)
