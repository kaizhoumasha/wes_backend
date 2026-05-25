import ast
import importlib.util
from pathlib import Path

from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import _SUPPORTED_INTENT_KINDS, RuntimeIntentEffectApplier

REPO_ROOT = Path(__file__).resolve().parents[2]

REMOVED_20260511_PROTOTYPE_MODULES = {
    "src.workline_runtime.material_run",
    "src.workline_runtime.runtime_event",
    "src.workline_runtime.material_flow_engine",
    "src.workline_runtime.projections",
    "src.workline_runtime.metrics",
    "src.workline_runtime.alerts",
}

REMOVED_LEGACY_STATE_MACHINE_MODULES = {
    "src.workline_runtime.types",
    "src.workline_runtime.transition_validator",
    "src.workline_runtime.state_machine",
}


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _production_python_files() -> list[Path]:
    return sorted((REPO_ROOT / "src").rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_20260511_prototype_modules_are_not_part_of_runtime_package() -> None:
    for module_name in REMOVED_20260511_PROTOTYPE_MODULES:
        assert not _module_exists(module_name), f"{module_name} must stay archived/removed"


def test_legacy_state_machine_modules_are_not_part_of_runtime_package() -> None:
    for module_name in REMOVED_LEGACY_STATE_MACHINE_MODULES:
        assert not _module_exists(module_name), f"{module_name} must not be reintroduced"


def test_production_code_does_not_import_removed_runtime_modules() -> None:
    forbidden_modules = REMOVED_20260511_PROTOTYPE_MODULES | REMOVED_LEGACY_STATE_MACHINE_MODULES

    offenders: list[str] = []
    for path in _production_python_files():
        imported_modules = _imported_modules(path)
        for module_name in forbidden_modules:
            if module_name in imported_modules:
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module_name}")

    assert offenders == []


def test_runtime_intent_effect_applier_supports_active_intent_kinds() -> None:
    active_runtime_intent_kinds = set(RuntimeIntentKind) - {RuntimeIntentKind.ROUTE}

    assert RuntimeIntentEffectApplier is not None
    assert active_runtime_intent_kinds <= _SUPPORTED_INTENT_KINDS
