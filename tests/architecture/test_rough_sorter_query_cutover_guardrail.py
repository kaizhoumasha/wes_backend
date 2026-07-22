"""rough-sorter QUERY 切换后旧专用 capability 必须永久归零。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_PACKAGE = REPO_ROOT / "src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission"
LEGACY_IDENTITY = "wms." + "rough_sorter_inventory_admission"
LEGACY_IMPORT = "src.app.runtime.system_capabilities.wms." + "rough_sorter_inventory_admission"


def test_legacy_rough_sorter_query_capability_package_is_absent() -> None:
    assert not LEGACY_PACKAGE.exists()


def test_legacy_identity_and_import_are_absent_from_executable_python() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    inventory_guard = REPO_ROOT / "tests/architecture/test_northbound_wms_operation_inventory.py"
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
        for path in sorted(root.rglob("*.py")):
            if path.resolve() in {this_file, inventory_guard}:
                continue
            source = path.read_text(encoding="utf-8")
            if LEGACY_IDENTITY in source or LEGACY_IMPORT in source:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_generated_and_plugin_indexes_use_only_generic_query_identity() -> None:
    from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_IDENTITIES
    from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION

    generic_identity = ("wms.inventory.query_inventory", "v1")
    assert generic_identity in SYSTEM_CAPABILITY_IDENTITIES
    assert generic_identity in DEFINITION.allowed_capabilities
