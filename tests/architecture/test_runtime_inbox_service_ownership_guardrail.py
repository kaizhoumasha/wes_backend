"""RuntimeInboxService 所有权与导入方向 guardrail。"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_PACKAGE = "src.app.runtime.orchestration.services.runtime_inbox"
OLD_SERVICE_MODULE = "src.app.runtime.orchestration.consumers.runtime_inbox_service"
PUBLIC_SERVICE_SYMBOLS = {
    "RuntimeInboxAcceptResult",
    "RuntimeInboxConflict",
    "RuntimeInboxCorrelationUnavailable",
    "RuntimeInboxPayloadTooLarge",
    "RuntimeInboxReplayResult",
    "RuntimeInboxService",
    "RuntimeInboxSessionOwnershipConflict",
    "runtime_inbox_service",
}


def test_runtime_inbox_service_has_single_public_owner() -> None:
    package = importlib.import_module(SERVICE_PACKAGE)

    assert set(package.__all__) >= PUBLIC_SERVICE_SYMBOLS
    assert set(vars(package)) >= PUBLIC_SERVICE_SYMBOLS
    assert not (REPO_ROOT / "src/app/runtime/orchestration/consumers/runtime_inbox_service.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(OLD_SERVICE_MODULE)


def test_active_python_sources_do_not_import_old_runtime_inbox_service() -> None:
    roots = (REPO_ROOT / "src", REPO_ROOT / "tests", REPO_ROOT / "scripts")
    offenders: list[str] = []
    for root in roots:
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom) and node.module == OLD_SERVICE_MODULE) or (
                    isinstance(node, ast.Import) and any(alias.name == OLD_SERVICE_MODULE for alias in node.names)
                ):
                    offenders.append(source.relative_to(REPO_ROOT).as_posix())

    assert not offenders, f"仍从旧 consumers service 路径导入: {sorted(set(offenders))}"


def test_runtime_inbox_package_modules_do_not_import_package_boundary() -> None:
    package_dir = REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox"
    offenders: list[str] = []
    for source in package_dir.glob("*.py"):
        if source.name == "__init__.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.module == SERVICE_PACKAGE) or (
                isinstance(node, ast.Import) and any(alias.name == SERVICE_PACKAGE for alias in node.names)
            ):
                offenders.append(source.name)

    assert not offenders, f"包内模块不得经 package __init__ 自引用: {sorted(set(offenders))}"


def test_runtime_inbox_service_does_not_depend_on_processor() -> None:
    service_file = REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py"
    tree = ast.parse(service_file.read_text(encoding="utf-8"), filename=str(service_file))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    assert not any("runtime_inbox_processor" in module for module in imported_modules)
