"""北向 WMS operation 遗留消费者清点守护。"""

from __future__ import annotations

import csv
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO_ROOT / "docs" / "architecture" / "northbound-wms-operation-inventory.csv"
ADR_PATH = REPO_ROOT / "docs" / "architecture" / "adr" / "2026-07-21-wms-operation-identity.md"
GUARD_PATH = Path(__file__).resolve()

LEGACY_IDENTITY_TARGETS = {
    "WmsInventoryQueryPort.query_inventory": "wms.inventory.query_inventory@v1",
    "wms.rough_sorter_inventory_admission": "wms.inventory.query_inventory@v1",
    "rough_sorter_inventory_admission": "wms.inventory.query_inventory@v1",
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


def _discover_references() -> set[tuple[str, str, str]]:
    discovered: set[tuple[str, str, str]] = set()
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
                if legacy_identity in text or (
                    legacy_identity == "rough_sorter_inventory_admission"
                    and "src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/" in relative_path
                ):
                    discovered.add((category, relative_path, target_identity))
            if category == "test":
                for operation_name, target_identity in TEST_OPERATION_TARGETS.items():
                    if operation_name in text:
                        discovered.add((category, relative_path, target_identity))
    return discovered


def _iter_markdown_files(relative_paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative_path in relative_paths:
        files.extend((REPO_ROOT / relative_path).rglob("*.md"))
    return sorted(set(files))


def test_inventory_covers_every_discovered_legacy_reference() -> None:
    """真实扫描结果与清点表必须双向对应，禁止漏项或无来源静态罗列。"""
    rows = _read_inventory()
    inventoried = {
        (row["category"], row["source_path"], row["target_operation_identity"])
        for row in rows
        if not row["source_path"].startswith("<missing:")
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
    """现状无 operation 级指标时必须逐 operation 登记缺口，不能误报为已覆盖。"""
    metric_rows = [row for row in _read_inventory() if row["category"] == "metric"]
    assert {row["target_operation_identity"] for row in metric_rows} == set(LEGACY_IDENTITY_TARGETS.values())
    assert all(row["source_path"] == "<missing:operation_metric>" for row in metric_rows)
    assert all(row["disposition"] == "REWRITE" for row in metric_rows)

    production_text = "\n".join(path.read_text(encoding="utf-8") for path in _iter_python_files(SOURCE_PATHS))
    metric_lines = [
        line
        for line in production_text.splitlines()
        if re.search(r"metric|counter|histogram|gauge", line, re.IGNORECASE)
        and any(identity in line for identity in LEGACY_IDENTITY_TARGETS)
    ]
    assert metric_lines == []


def test_adr_and_inventory_share_the_same_stable_operation_identities() -> None:
    """ADR 决策出的稳定 identity 必须与清点表目标集合完全一致。"""
    inventory_identities = {row["target_operation_identity"] for row in _read_inventory()}
    adr_text = ADR_PATH.read_text(encoding="utf-8")
    adr_identities = set(re.findall(r"^\| `([^`]+@v1)` \|", adr_text, flags=re.MULTILINE))

    assert adr_identities == inventory_identities
    for required_boundary in ("typed contract", "catalog", "Provider", "删除门禁", "不预建空壳", "不保留兼容"):
        assert required_boundary in adr_text
