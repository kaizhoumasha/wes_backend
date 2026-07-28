"""RuntimeInboxService 所有权与导入方向 guardrail。"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_PACKAGE = "src.app.runtime.orchestration.services.runtime_inbox"
CONCRETE_SERVICE_MODULE = f"{SERVICE_PACKAGE}.runtime_inbox_service"
OLD_SERVICE_MODULE = "src.app.runtime.orchestration.consumers.runtime_inbox_service"
PUBLIC_SERVICE_SYMBOLS = {
    "RuntimeInboxAcceptResult",
    "RuntimeInboxAuditPersistenceFailed",
    "RuntimeInboxConflict",
    "RuntimeInboxCorrelationUnavailable",
    "RuntimeInboxNotFound",
    "RuntimeInboxPayloadTooLarge",
    "RuntimeInboxReplayNotAllowed",
    "RuntimeInboxReplayResult",
    "RuntimeInboxService",
    "RuntimeInboxSessionOwnershipConflict",
    "runtime_inbox_service",
    "validate_replay_envelope",
}
PUBLIC_PACKAGE_SYMBOLS = PUBLIC_SERVICE_SYMBOLS | {
    "ProcessResult",
    "RuntimeInboxProcessorBridge",
    "RuntimeInboxValidationService",
    "RuntimeInboxWriteBackService",
    "ValidationOutcome",
}


def _imports_concrete_runtime_inbox_service(source: Path) -> bool:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == CONCRETE_SERVICE_MODULE:
            return True
        if isinstance(node, ast.Import) and any(alias.name == CONCRETE_SERVICE_MODULE for alias in node.names):
            return True
        if isinstance(node, ast.Constant) and node.value == CONCRETE_SERVICE_MODULE:
            return True
    return False


def _package_boundary_import_offenders(package_dir: Path) -> list[str]:
    offenders: list[str] = []
    for source in package_dir.rglob("*.py"):
        if source.name == "__init__.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.module == SERVICE_PACKAGE) or (
                isinstance(node, ast.Import) and any(alias.name == SERVICE_PACKAGE for alias in node.names)
            ):
                offenders.append(source.relative_to(package_dir).as_posix())
    return offenders


def test_runtime_inbox_service_has_single_public_owner() -> None:
    package = importlib.import_module(SERVICE_PACKAGE)
    concrete_module = importlib.import_module(CONCRETE_SERVICE_MODULE)

    assert set(concrete_module.__all__) == PUBLIC_SERVICE_SYMBOLS
    assert set(package.__all__) == PUBLIC_PACKAGE_SYMBOLS
    for symbol in PUBLIC_SERVICE_SYMBOLS:
        assert getattr(package, symbol) is getattr(concrete_module, symbol), f"{symbol} 必须复用具体模块同一对象"
    assert not (REPO_ROOT / "src/app/runtime/orchestration/consumers/runtime_inbox_service.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(OLD_SERVICE_MODULE)


def test_replay_source_validation_types_stay_internal_to_runtime_inbox_package() -> None:
    package = importlib.import_module(SERVICE_PACKAGE)

    assert not hasattr(package, "RuntimeInboxReplaySourceValidation")
    assert not hasattr(package, "RuntimeInboxReplaySourceValidator")


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
    offenders = _package_boundary_import_offenders(package_dir)

    assert not offenders, f"包内模块不得经 package __init__ 自引用: {sorted(set(offenders))}"


def test_package_boundary_guardrail_scans_nested_modules_and_skips_nested_package_initializers(
    tmp_path: Path,
) -> None:
    nested_package = tmp_path / "nested"
    nested_package.mkdir()
    forbidden_import = f"from {SERVICE_PACKAGE} import RuntimeInboxService\n"
    (nested_package / "__init__.py").write_text(forbidden_import, encoding="utf-8")
    (nested_package / "worker.py").write_text(forbidden_import, encoding="utf-8")

    assert _package_boundary_import_offenders(tmp_path) == ["nested/worker.py"]


def test_production_modules_outside_runtime_inbox_package_use_public_boundary() -> None:
    package_dir = REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox"
    offenders = [
        source.relative_to(REPO_ROOT).as_posix()
        for source in (REPO_ROOT / "src").rglob("*.py")
        if not source.is_relative_to(package_dir) and _imports_concrete_runtime_inbox_service(source)
    ]

    assert not offenders, f"包外生产模块不得直连 RuntimeInboxService 具体模块: {offenders}"


@pytest.mark.parametrize(
    "source_text",
    (
        f"from {CONCRETE_SERVICE_MODULE} import RuntimeInboxService\n",
        f"import {CONCRETE_SERVICE_MODULE}\n",
        f'import importlib\nimportlib.import_module("{CONCRETE_SERVICE_MODULE}")\n',
        f'__import__("{CONCRETE_SERVICE_MODULE}")\n',
    ),
)
def test_concrete_service_import_guardrail_covers_static_and_dynamic_imports(
    tmp_path: Path,
    source_text: str,
) -> None:
    source = tmp_path / "production_module.py"
    source.write_text(source_text, encoding="utf-8")

    assert _imports_concrete_runtime_inbox_service(source)


def test_runtime_inbox_service_does_not_depend_on_processor() -> None:
    service_file = REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py"
    tree = ast.parse(service_file.read_text(encoding="utf-8"), filename=str(service_file))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    assert not any("runtime_inbox_processor" in module for module in imported_modules)
