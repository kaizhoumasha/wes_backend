"""北向 WMS operation 遗留消费者清点守护。"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

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
METRIC_OWNER_PATHS = (
    "src/app/runtime/orchestration/observability.py",
    "src/app/runtime/orchestration/services",
    "src/app/wms_integration",
    "src/app/callback/services",
    "src/app/device/services",
    "src/celery_app/tasks",
)
METRIC_SIGNAL_MARKERS = (
    "RuntimeObservabilitySignal(",
    "emit_metric(",
    "observability_emit",
    "runtime_observability_registry.emit(",
    '"metric"',
    '"metric+',
)
STRUCTURED_OPERATION_LABELS = ("operation_identity", "operation_name", "operation_kind")


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


def _contains_exact_identity(text: str, identity: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(identity)}(?![A-Za-z0-9_])", text) is not None


def _without_full_identities(text: str) -> str:
    full_identities = (*LEGACY_IDENTITY_TARGETS, *LEGACY_IDENTITY_TARGETS.values())
    for identity in sorted(set(full_identities), key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(identity)}(?![A-Za-z0-9_])", "", text)
    return text


def _discover_split_port_method_references(text: str) -> set[tuple[str, str]]:
    discovered: set[tuple[str, str]] = set()
    port_methods = {
        tuple(legacy_identity.rsplit(".", 1)): target_identity
        for legacy_identity, target_identity in LEGACY_IDENTITY_TARGETS.items()
        if legacy_identity.startswith("Wms") and "." in legacy_identity
    }
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        code_identities = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line))
        for (port_name, method_name), target_identity in port_methods.items():
            if {port_name, method_name} <= code_identities:
                discovered.add((f"{port_name}.{method_name}", target_identity))
    return discovered


def _discover_references() -> set[tuple[str, str, str, str]]:
    discovered: set[tuple[str, str, str, str]] = set()
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
                if _contains_exact_identity(text, legacy_identity) or (
                    legacy_identity == "rough_sorter_inventory_admission"
                    and "src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/" in relative_path
                ):
                    discovered.add((category, relative_path, legacy_identity, target_identity))
            if category == "documentation":
                for legacy_identity, target_identity in _discover_split_port_method_references(text):
                    discovered.add((category, relative_path, legacy_identity, target_identity))
            if category == "test":
                residual_text = _without_full_identities(text)
                for operation_name, target_identity in TEST_OPERATION_TARGETS.items():
                    if _contains_exact_identity(residual_text, operation_name):
                        discovered.add((category, relative_path, operation_name, target_identity))
    return discovered


def _discover_metric_references() -> set[tuple[str, str, str, str]]:
    discovered: set[tuple[str, str, str, str]] = set()
    for path in _iter_python_files(METRIC_OWNER_PATHS):
        text = path.read_text(encoding="utf-8")
        if not any(marker in text for marker in METRIC_SIGNAL_MARKERS):
            continue
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for legacy_identity, target_identity in LEGACY_IDENTITY_TARGETS.items():
            if _contains_exact_identity(text, legacy_identity):
                discovered.add(("metric", relative_path, legacy_identity, target_identity))
        for target_identity in set(LEGACY_IDENTITY_TARGETS.values()):
            if _contains_exact_identity(text, target_identity):
                discovered.add(("metric", relative_path, target_identity, target_identity))

        residual_text = _without_full_identities(text)
        has_structured_label = any(
            re.search(rf"[\"']{label}[\"']\s*:", residual_text) for label in STRUCTURED_OPERATION_LABELS
        )
        if has_structured_label:
            for operation_name, target_identity in TEST_OPERATION_TARGETS.items():
                if _contains_exact_identity(residual_text, operation_name):
                    discovered.add(("metric", relative_path, operation_name, target_identity))
    return discovered


def _iter_markdown_files(relative_paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative_path in relative_paths:
        files.extend((REPO_ROOT / relative_path).rglob("*.md"))
    return sorted(set(files))


def test_reference_scanner_preserves_legacy_identity_and_parses_split_port_method_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Port 与方法分列时仍按完整 legacy identity 清点，且不能命中词片段。"""
    document_path = tmp_path / "docs" / "architecture" / "port-table.md"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(
        """| Port | 关键方法 |
| --- | --- |
| `WmsInventoryQueryPort` | `query_inventory` / `query_inventory_cache` |
| `WmsFulfillmentPort` | `full_box_exchange` / `notify_pkg_binding` |
| `WmsInventoryQueryPortability` | `query_inventory_preview` |
""",
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_PATHS", ())
    monkeypatch.setattr(module, "TEST_PATHS", ())
    monkeypatch.setattr(module, "DOCUMENTATION_PATHS", ("docs/architecture",))

    assert _discover_references() == {
        (
            "documentation",
            "docs/architecture/port-table.md",
            "WmsInventoryQueryPort.query_inventory",
            "wms.inventory.query_inventory@v1",
        ),
        (
            "documentation",
            "docs/architecture/port-table.md",
            "WmsFulfillmentPort.full_box_exchange",
            "wms.fulfillment.full_box_exchange@v1",
        ),
        (
            "documentation",
            "docs/architecture/port-table.md",
            "WmsFulfillmentPort.notify_pkg_binding",
            "wms.fulfillment.notify_pkg_binding@v1",
        ),
    }


def test_metric_scanner_recognizes_all_supported_operation_identity_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指标 owner 中 legacy、目标 identity 与结构化 label 都必须进入双向清点。"""
    metric_root = tmp_path / "src" / "app" / "runtime" / "orchestration"
    metric_root.mkdir(parents=True)
    (metric_root / "legacy_metric.py").write_text(
        'WMS_OPERATION_COUNTER = {"kind": "metric", "operation": "WmsInventoryQueryPort.query_inventory"}\n',
        encoding="utf-8",
    )
    (metric_root / "target_metric.py").write_text(
        'emit_metric("wms.operation", {"operation_identity": "wms.inventory.confirm_inbound@v1"})\n',
        encoding="utf-8",
    )
    (metric_root / "label_metric.py").write_text(
        'emit_metric("wms.operation", {"operation_name": "notify_pkg_binding"})\n'
        'emit_metric("wms.operation.preview", {"operation_name": "notify_pkg_binding_preview"})\n',
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "METRIC_OWNER_PATHS", ("src/app/runtime/orchestration",))

    assert _discover_metric_references() == {
        (
            "metric",
            "src/app/runtime/orchestration/legacy_metric.py",
            "WmsInventoryQueryPort.query_inventory",
            "wms.inventory.query_inventory@v1",
        ),
        (
            "metric",
            "src/app/runtime/orchestration/target_metric.py",
            "wms.inventory.confirm_inbound@v1",
            "wms.inventory.confirm_inbound@v1",
        ),
        (
            "metric",
            "src/app/runtime/orchestration/label_metric.py",
            "notify_pkg_binding",
            "wms.fulfillment.notify_pkg_binding@v1",
        ),
    }


def test_inventory_covers_every_discovered_legacy_reference() -> None:
    """真实扫描结果与清点表必须双向对应，禁止漏项或无来源静态罗列。"""
    rows = _read_inventory()
    inventoried = {
        (row["category"], row["source_path"], row["legacy_identity"], row["target_operation_identity"])
        for row in rows
        if row["category"] != "metric" and not row["source_path"].startswith("<missing:")
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
    """真实 operation 指标与清点表按来源双向对应，其余 operation 显式登记缺口。"""
    metric_rows = [row for row in _read_inventory() if row["category"] == "metric"]
    real_rows = {
        (row["category"], row["source_path"], row["legacy_identity"], row["target_operation_identity"])
        for row in metric_rows
        if not row["source_path"].startswith("<missing:")
    }
    discovered = _discover_metric_references()
    assert discovered == real_rows, (
        f"指标清点表与真实 operation 指标不一致；漏项={sorted(discovered - real_rows)}；"
        f"无来源项={sorted(real_rows - discovered)}"
    )

    discovered_targets = {target_identity for _, _, _, target_identity in discovered}
    missing_rows = [row for row in metric_rows if row["source_path"] == "<missing:operation_metric>"]
    assert {row["target_operation_identity"] for row in missing_rows} == (
        set(LEGACY_IDENTITY_TARGETS.values()) - discovered_targets
    )
    assert all(row["legacy_identity"] == "<absent>" for row in missing_rows)
    assert all(row["disposition"] == "REWRITE" for row in metric_rows)


def test_adr_and_inventory_share_the_same_stable_operation_identities() -> None:
    """ADR 决策出的稳定 identity 必须与清点表目标集合完全一致。"""
    inventory_identities = {row["target_operation_identity"] for row in _read_inventory()}
    adr_text = ADR_PATH.read_text(encoding="utf-8")
    adr_identities = set(re.findall(r"^\| `([^`]+@v1)` \|", adr_text, flags=re.MULTILINE))

    assert adr_identities == inventory_identities
    for required_boundary in ("typed contract", "catalog", "Provider", "删除门禁", "不预建空壳", "不保留兼容"):
        assert required_boundary in adr_text
