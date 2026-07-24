"""`confirm_inbound` 遗留链必须随 T9 硬切换归零。"""

from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LEGACY_SOURCE_MARKERS = {
    Path("src/app/wms_integration/ports/inventory_transaction.py"): ("def confirm_inbound(",),
    Path("src/app/wms_integration/models/ports.py"): (
        "class ConfirmInboundRequest(",
        "class ConfirmInboundResponse(",
        '"confirm_inbound",',
    ),
    Path("src/app/wms_integration/models/__init__.py"): (
        "ConfirmInboundRequest",
        "ConfirmInboundResponse",
    ),
    Path("src/app/wms_integration/services/typed_ports.py"): (
        "ConfirmInboundRequest",
        "ConfirmInboundResponse",
        "async def confirm_inbound(",
        'self._execute("confirm_inbound"',
    ),
    Path("src/app/wms_integration/services/endpoint_config.py"): (
        '"confirm_inbound": (',
        "WMS_SYNC_CONFIRM_INBOUND_PATH",
    ),
    Path("src/app/contracts/external_contract_profile_catalog.py"): ("WmsInventoryTransactionPort.confirm_inbound",),
    Path("src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py"): (
        "WmsInventoryTransactionPort.confirm_inbound",
    ),
    Path("src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py"): (
        "WmsInventoryTransactionPort.confirm_inbound",
    ),
}

LEGACY_REFERENCE_ROOTS = (
    Path("src"),
    Path("scripts"),
    Path("tests"),
    Path("docs/architecture"),
)
LEGACY_REFERENCE_EXCLUSIONS = {
    Path("tests/architecture/test_confirm_inbound_legacy_cutover.py"),
    Path("tests/architecture/test_northbound_wms_operation_inventory.py"),
    Path("docs/architecture/adr/2026-07-21-wms-operation-identity.md"),
}


def test_confirm_inbound_legacy_source_contracts_are_deleted() -> None:
    findings: list[str] = []
    for relative_path, markers in LEGACY_SOURCE_MARKERS.items():
        source_path = REPO_ROOT / relative_path
        if not source_path.is_file():
            continue
        content = source_path.read_text(encoding="utf-8")
        findings.extend(f"{relative_path}: {marker}" for marker in markers if marker in content)
    assert findings == []


def test_confirm_inbound_legacy_string_reference_has_no_active_reference() -> None:
    findings: list[str] = []
    for root in LEGACY_REFERENCE_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*")):
            if not path.is_file() or path.suffix not in {".csv", ".md", ".py"}:
                continue
            relative_path = path.relative_to(REPO_ROOT)
            if relative_path in LEGACY_REFERENCE_EXCLUSIONS or "archive" in relative_path.parts:
                continue
            if "WmsInventoryTransactionPort.confirm_inbound" in path.read_text(encoding="utf-8"):
                findings.append(str(relative_path))
    assert findings == []


def test_confirm_inbound_t9_inventory_rows_are_zero() -> None:
    inventory_path = REPO_ROOT / "docs/architecture/northbound-wms-operation-inventory.csv"
    with inventory_path.open(encoding="utf-8", newline="") as file:
        rows = tuple(csv.DictReader(file))

    assert [row["entry_id"] for row in rows if row["target_operation_identity"] == CONTRACT_IDENTITY] == []


CONTRACT_IDENTITY = "wms.inventory.confirm_inbound@v1"
