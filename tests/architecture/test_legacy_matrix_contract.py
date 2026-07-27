"""Legacy cleanup matrix generation contract."""

from __future__ import annotations

import ast
import csv
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from scripts import generate_legacy_matrix
from scripts.generate_legacy_matrix import parse_entries

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ACTIVE_FOUNDATION_PATHS = frozenset(
    {
        "src/app/workline/models/migration_inventory.py",
        "src/app/workline/models/migration_matrix.py",
        "src/app/workline/services/migration_inventory_service.py",
        "src/app/workline/services/migration_matrix_service.py",
        "tests/workline_runtime/test_workline_migration_inventory_models.py",
        "tests/workline_runtime/test_workline_migration_inventory_service.py",
        "tests/workline_runtime/test_workline_migration_matrix_service.py",
    }
)

EXPECTED_ACTIVE_PLATFORM_PREFIXES = (
    "tests/workline_plugins/rough_sorter/",
    "tests/workline_plugins/smt_sorting_inbound/",
    "tests/workline_runtime/extensions/",
    "tests/workline_runtime/system_capabilities/",
)

EXPECTED_ACTIVE_PLATFORM_PATHS = frozenset(
    {
        "src/app/workline/models/plugin_binding.py",
        "src/app/workline/repositories/plugin_binding_repository.py",
        "src/app/workline/services/plugin_binding_service.py",
        "tests/workline_plugins/test_conformance_contract.py",
        "tests/workline_plugins/test_generated_facts_contract.py",
        "tests/workline_runtime/test_workline_session_repository_versioning.py",
    }
)


def _entry_by_id(entry_id: str):
    entries = parse_entries()
    return next((entry for entry in entries if entry.entry_id == entry_id), None)


def test_removed_inbox_processor_has_no_inventory_mapping_or_entries():
    """已物理删除的 processor 不得继续作为 legacy cleanup 目标。"""
    legacy_path = "src/app/workline/services/inbox_batch_processor.py"

    assert legacy_path not in generate_legacy_matrix.MIGRATED_SERVICE_IMPLS
    assert not any(entry.relative_path == legacy_path for entry in parse_entries())


def test_active_inventory_foundation_is_not_legacy_cleanup_scope():
    """当前迁移清单基础能力不得被误登记为待迁移或待删除入口。"""
    assert generate_legacy_matrix.ACTIVE_FOUNDATION_PATHS == EXPECTED_ACTIVE_FOUNDATION_PATHS

    parsed_paths = {entry.relative_path for entry in parse_entries() if entry.notes != "guardrail_seed_scope"}
    assert parsed_paths.isdisjoint(EXPECTED_ACTIVE_FOUNDATION_PATHS)


def test_active_extension_platform_is_not_legacy_cleanup_scope():
    assert generate_legacy_matrix.ACTIVE_PLATFORM_PREFIXES == EXPECTED_ACTIVE_PLATFORM_PREFIXES
    assert generate_legacy_matrix.ACTIVE_PLATFORM_PATHS == EXPECTED_ACTIVE_PLATFORM_PATHS

    entries = parse_entries()
    assert not any(entry.relative_path.startswith(EXPECTED_ACTIVE_PLATFORM_PREFIXES) for entry in entries)
    assert not any(entry.symbol_or_route == "TestRoughSorterConformance" for entry in entries)
    assert not any(
        entry.relative_path in generate_legacy_matrix.ACTIVE_PLATFORM_PATHS and entry.notes != "guardrail_seed_scope"
        for entry in entries
    )


def test_runtime_extension_allowlist_uses_per_file_legacy_entries():
    entries = {entry.entry_id for entry in parse_entries()}
    allowlist = (REPO_ROOT / "scripts" / "architecture-guardrails.allowlist").read_text(encoding="utf-8")

    for row in allowlist.splitlines():
        if not row.startswith(("LEGACY_CAPABILITY_ROUTING_IMPORT|", "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION|")):
            continue
        rule, path, _reason, _expires_at, legacy_entry_id, _drop_phase = row.split("|")
        expected = f"legacy:{path}:<file>#{rule}"
        assert legacy_entry_id == expected
        assert expected in entries


def test_rebuild_or_move_entries_have_target_and_blocking_tests():
    """rebuild/move 项必须可执行：目标载体和阻塞测试都不能空。"""
    entries = parse_entries()
    actionable_entries = [entry for entry in entries if entry.strategy in {"rebuild", "move"}]

    assert actionable_entries
    assert all(entry.target_path or entry.target_capability for entry in actionable_entries)
    assert all(entry.blocking_tests for entry in actionable_entries)


def test_generated_csv_contains_required_migration_columns():
    matrix_path = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"

    with open(matrix_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames

    assert "target_path" in (fieldnames or [])
    assert "target_capability" in (fieldnames or [])
    assert "blocking_tests" in (fieldnames or [])


def test_generated_csv_uses_lf_line_endings():
    """生成的 CSV 必须使用 LF，避免 git diff --check 将 CRLF 判成 trailing whitespace。"""
    matrix_path = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"

    assert b"\r\n" not in matrix_path.read_bytes()


def test_git_grep_falls_back_when_git_metadata_is_unavailable(monkeypatch):
    """CI 镜像无 .git 时，矩阵生成仍必须能扫描源码。"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=128, stdout="", stderr="fatal: not a git repository")

    monkeypatch.setattr(generate_legacy_matrix.subprocess, "run", fake_run)

    lines = generate_legacy_matrix.git_grep(r"^class WorkLine", ["src/app/workline/models"])

    assert any(line.startswith("src/app/workline/models/workline.py:") for line in lines)


def test_generated_csv_matches_parse_entries_for_required_fields():
    """提交的 CSV 必须与生成器输出一致，防止人工漂移或漏填。"""
    matrix_path = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
    expected = {
        entry.entry_id: {
            "strategy": entry.strategy,
            "target_path": entry.target_path,
            "target_capability": entry.target_capability,
            "blocking_tests": entry.blocking_tests,
            "drop_phase": entry.drop_phase,
        }
        for entry in parse_entries()
    }

    with open(matrix_path, newline="", encoding="utf-8") as f:
        rows = {row["entry_id"]: row for row in csv.DictReader(f)}

    assert rows.keys() == expected.keys()
    for entry_id, fields in expected.items():
        for field, value in fields.items():
            assert rows[entry_id][field] == value


@pytest.mark.parametrize(
    ("business_semantics", "path", "symbol", "expected_capability"),
    [
        pytest.param(
            "[phase" + "4] NG 退货/处理业务流程",
            "src/app/workline/services/ng_return_item_service.py",
            "NgReturnItemService",
            "NgReturnCapability.process",
            id="ng-return",
        ),
        pytest.param(
            "[phase" + "4] 单层机架编排业务流程",
            "tests/workline_runtime/test_single_layer_rack_orchestration_service.py",
            "test_station_claim_active_status_accepts_system_outbox_status_enum",
            "SingleLayerRackCapability.orchestrate",
            id="single-layer-rack",
        ),
        pytest.param(
            "[phase" + "4] Bin Cell 预约业务流程",
            "tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py",
            "test_reconciling_reservation_cannot_be_released_by_normal_failure_path",
            "BinCellReservationCapability.reserve",
            id="bin-cell-reconciling",
        ),
    ],
)
def test_material_flow_target_marker_classification(
    business_semantics: str,
    path: str,
    symbol: str,
    expected_capability: str,
) -> None:
    target_path, target_capability = generate_legacy_matrix.resolve_migration_target(
        business_semantics,
        "test",
        path,
        symbol,
        "rebuild",
    )

    assert target_path == "src/app/runtime/capabilities/material_flow/"
    assert target_capability == expected_capability


def test_markdown_summary_matches_generated_csv():
    """Markdown 摘要中的硬编码统计必须与 CSV 同步。"""
    matrix_path = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
    doc_text = (REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.md").read_text(encoding="utf-8")

    with open(matrix_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total_entries = len(rows)
    material_flow_carrier_field = "phase" + "4_carrier"
    material_flow_carrier_label = "Phase " + "4"
    material_flow_carrier = sum(row[material_flow_carrier_field] == "True" for row in rows)
    pending_review = sum(row["classification_status"] == "pending-review" for row in rows)

    assert f"legacy-cleanup-matrix.csv（{total_entries} 条" in doc_text
    assert f"| **total_entries** | **{total_entries}** |" in doc_text
    assert (
        f"| {material_flow_carrier_field}（承载 {material_flow_carrier_label} 业务语义） | {material_flow_carrier} |"
        in doc_text
    )
    assert f"| pending-review | {pending_review} |" in doc_text

    for field in ("entry_type", "strategy", "drop_phase", "current_owner"):
        for value, count in Counter(row[field] for row in rows).items():
            assert f"| {value} | {count} |" in doc_text


def _exported_symbols_from_all_assignment(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if isinstance(node.value, ast.List | ast.Tuple):
            symbols.update(
                item.value for item in node.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return symbols


def test_runtime_and_plugin_all_exports_are_inventory_entries():
    """Plugin/runtime artifact 发现命令包含 __all__，导出符号必须入矩阵。"""
    entries = {entry.entry_id for entry in parse_entries()}
    missing: list[str] = []
    for root in (REPO_ROOT / "src" / "workline_runtime", REPO_ROOT / "src" / "workline_plugins"):
        for path in root.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            for symbol in _exported_symbols_from_all_assignment(path):
                entry_id = f"legacy:{rel}:{symbol}"
                if entry_id not in entries:
                    missing.append(entry_id)

    assert missing == []


def test_capability_implementation_import_device_seed_targets_device_command_port():
    """只 import device 实现的 CAPABILITY_IMPLEMENTATION_IMPORT seed 必须指向 DeviceCommandPort。"""
    # AUTHORITY_METADATA_BOUNDARY 收敛后:
    # device_command_gateway 物理迁入 runtime/orchestration/services/，
    # CAPABILITY_IMPLEMENTATION_IMPORT seed 路径跟随新位置。
    entry = _entry_by_id(
        "legacy:src/app/runtime/orchestration/services/device_command_gateway.py:<file>#CAPABILITY_IMPLEMENTATION_IMPORT"
    )

    assert entry is not None
    assert "capability import device 实现" in entry.business_semantics
    assert entry.target_path == "src/app/runtime/orchestration/ports/device_command.py"
    assert entry.target_capability == "DeviceCommandPort.dispatch"


def test_capability_implementation_import_wms_seed_targets_wms_fulfillment_port():
    """import wms_integration 实现的 CAPABILITY_IMPLEMENTATION_IMPORT seed 仍指向 WMS 履约 port。"""
    entry = _entry_by_id(
        "legacy:src/app/workline/services/single_layer_rack_orchestration_service.py:<file>#CAPABILITY_IMPLEMENTATION_IMPORT"
    )

    assert entry is not None
    assert entry.business_semantics == "capability import wms_integration 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed)"
    assert entry.target_path == "src/app/runtime/orchestration/ports/wms_fulfillment.py"
    assert entry.target_capability == "WmsFulfillmentPort.request_transport"
