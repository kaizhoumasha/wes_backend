"""Phase 0 legacy cleanup matrix 生成契约。

这些测试锁定 SPEC P0-002 的入口粒度和必填迁移字段，避免矩阵误绿。
"""

from __future__ import annotations

import ast
import csv
from pathlib import Path

from scripts.generate_legacy_matrix import parse_entries

REPO_ROOT = Path(__file__).resolve().parents[2]


def _entry_by_id(entry_id: str):
    entries = parse_entries()
    return next((entry for entry in entries if entry.entry_id == entry_id), None)


def test_service_inventory_includes_module_level_functions():
    """service inventory 必须覆盖 services 下的 class/def/async def。"""
    entry = _entry_by_id(
        "legacy:src/app/workline/services/inbox_batch_processor.py:build_workline_runtime_session_updated_event_payload"
    )

    assert entry is not None
    assert entry.entry_type == "service"


def test_rebuild_or_move_entries_have_target_and_blocking_tests():
    """rebuild/move 项在 Phase 0 必须可执行：目标载体和阻塞测试都不能空。"""
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


def test_ri3b_device_seed_targets_device_command_port():
    """只 import device 实现的 R-I3b seed 必须指向 DeviceCommandPort。"""
    entry = _entry_by_id("legacy:src/app/workline/services/device_command_gateway.py:<file>#R-I3b")

    assert entry is not None
    assert entry.business_semantics == "capability import device 实现 (R-I3b seed)"
    assert entry.target_path == "src/app/runtime/orchestration/ports/device_command.py"
    assert entry.target_capability == "DeviceCommandPort.dispatch"


def test_ri3b_wms_seed_targets_wms_fulfillment_port():
    """import wms_integration 实现的 R-I3b seed 仍指向 WMS 履约 port。"""
    entry = _entry_by_id("legacy:src/app/workline/services/single_layer_rack_orchestration_service.py:<file>#R-I3b")

    assert entry is not None
    assert entry.business_semantics == "capability import wms_integration 实现 (R-I3b seed)"
    assert entry.target_path == "src/app/runtime/orchestration/ports/wms_fulfillment.py"
    assert entry.target_capability == "WmsFulfillmentPort.request_transport"
