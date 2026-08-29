"""`notify_pkg_binding` 遗留链必须随 T10 硬切换归零。"""

from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_IDENTITY = "wms.fulfillment.notify_pkg_binding@v1"

LEGACY_SOURCE_MARKERS = {
    Path("src/app/contracts/external_contract_profile_catalog.py"): ("WmsFulfillmentPort.notify_pkg_binding",),
    Path("src/app/sys/services/endpoint_registry.py"): (
        '"WMS_FULFILLMENT":',
        "WMS_FULFILLMENT_URL",
    ),
}

LEGACY_REFERENCE_ROOTS = (
    Path("src"),
    Path("scripts"),
    Path("tests"),
)
LEGACY_REFERENCE_EXCLUSIONS = {
    Path("tests/architecture/test_notify_pkg_binding_legacy_cutover.py"),
    Path("tests/architecture/test_northbound_wms_operation_inventory.py"),
}


def test_notify_pkg_binding_legacy_source_contracts_are_deleted() -> None:
    findings: list[str] = []
    for relative_path, markers in LEGACY_SOURCE_MARKERS.items():
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        findings.extend(f"{relative_path}: {marker}" for marker in markers if marker in content)
    assert findings == []


def test_notify_pkg_binding_legacy_port_and_target_have_no_active_reference() -> None:
    findings: list[str] = []
    for root in LEGACY_REFERENCE_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*")):
            if not path.is_file() or path.suffix not in {".csv", ".py", ".sh"}:
                continue
            relative_path = path.relative_to(REPO_ROOT)
            if relative_path in LEGACY_REFERENCE_EXCLUSIONS:
                continue
            content = path.read_text(encoding="utf-8")
            if "WmsFulfillmentPort.notify_pkg_binding" in content:
                findings.append(str(relative_path))
    assert findings == []


def test_notify_pkg_binding_inventory_switches_to_typed_adapter() -> None:
    inventory_path = REPO_ROOT / "docs/architecture/northbound-wms-operation-inventory.csv"
    with inventory_path.open(encoding="utf-8", newline="") as file:
        rows = tuple(csv.DictReader(file))

    matching = [row for row in rows if row["target_operation_identity"] == CONTRACT_IDENTITY]
    assert [(row["disposition"], row["owner"]) for row in matching] == [
        ("SWITCH", "src/app/wms_adapter/execution_confirmation_adapter.py:WmsExecutionConfirmationAdapter")
    ]
