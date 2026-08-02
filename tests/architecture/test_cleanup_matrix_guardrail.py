"""Legacy cleanup matrix audit trace 守护(F-5)。

验证 cleanup matrix 的 audit trace 维度：
- 全字段一致性(现有仅比 5 个迁移字段,F-5 比其余 8 个审计字段)
- entry_id 格式 `legacy:<relative_path>:<symbol>` 与列一致
- allowlist 的 legacy_entry_id 反向引用必须在 CSV 中存在
- classification_status 枚举收敛到 {final, pending-review}

这些校验锁定 CSV 作为 audit trace 的完整性:任何手动编辑 CSV 漂移、
allowlist 引用孤儿 entry、或 classification_status 越界都会被捕获。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from scripts.generate_legacy_matrix import parse_entries

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

_ENTRY_ID_RE = re.compile(r"^legacy:(?P<path>[^:]+):(?P<symbol>.+)$")
_VALID_CLASSIFICATION_STATUS = frozenset({"final", "pending-review"})


def _read_csv_rows() -> list[dict[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_csv_full_field_consistency_with_parse_entries():
    """CSV 全字段必须与 parse_entries() 输出一致(补现有 5 字段比较的缺口)。

    现有 `test_generated_csv_matches_parse_entries_for_required_fields` 仅比
    strategy/target_path/target_capability/blocking_tests/drop_phase。本测试补全
    entry_type / relative_path / symbol_or_route / current_owner /
    business_semantics / phase4_carrier / classification_status / risk 共 8 个
    审计字段,防止手动编辑 CSV 字段漂移。
    """
    expected = {entry.entry_id: entry for entry in parse_entries()}
    rows = {row["entry_id"]: row for row in _read_csv_rows()}

    assert rows.keys() == expected.keys(), "CSV entry_id 集合与生成器输出不一致"

    for entry_id, entry in expected.items():
        row = rows[entry_id]
        assert row["entry_type"] == entry.entry_type, f"{entry_id}: entry_type 漂移"
        assert row["relative_path"] == entry.relative_path, f"{entry_id}: relative_path 漂移"
        assert row["symbol_or_route"] == entry.symbol_or_route, f"{entry_id}: symbol_or_route 漂移"
        assert row["current_owner"] == entry.current_owner, f"{entry_id}: current_owner 漂移"
        assert row["business_semantics"] == entry.business_semantics, f"{entry_id}: business_semantics 漂移"
        assert row["phase4_carrier"] == str(entry.phase4_carrier), f"{entry_id}: phase4_carrier 漂移"
        assert row["classification_status"] == entry.classification_status, f"{entry_id}: classification_status 漂移"
        assert row["risk"] == entry.risk, f"{entry_id}: risk 漂移"


def test_entry_id_format_matches_columns():
    """每个 entry_id 必须为 `legacy:<relative_path>:<symbol>` 格式,
    且 path / symbol 部分与 CSV 的 relative_path / symbol_or_route 列一致。"""
    for row in _read_csv_rows():
        entry_id = row["entry_id"]
        m = _ENTRY_ID_RE.match(entry_id)
        assert m is not None, f"entry_id 格式非法: {entry_id}"
        assert m.group("path") == row["relative_path"], f"entry_id path 部分与 relative_path 列不一致: {entry_id}"
        assert m.group("symbol") == row["symbol_or_route"], (
            f"entry_id symbol 部分与 symbol_or_route 列不一致: {entry_id}"
        )


def test_entry_ids_unique():
    """entry_id 必须唯一(防 audit trace 重复记账)。"""
    rows = _read_csv_rows()
    ids = [row["entry_id"] for row in rows]
    duplicates = {eid for eid in ids if ids.count(eid) > 1}
    assert not duplicates, f"entry_id 重复: {duplicates}"


def test_classification_status_enum():
    """classification_status 必须收敛到 {final, pending-review}。"""
    for row in _read_csv_rows():
        status = row["classification_status"]
        assert status in _VALID_CLASSIFICATION_STATUS, f"entry {row['entry_id']} classification_status 越界: {status}"


def test_allowlist_legacy_entry_ids_exist_in_csv():
    """allowlist 第 5 列 legacy_entry_id 必须在 CSV entry_id 集合中存在。

    防止 allowlist 引用孤儿 entry(legacy_entry_id 指向已删除或拼错的 CSV 行),
    确保 allowlist 豁免与 audit trace 双向可追溯。
    """
    csv_ids = {row["entry_id"] for row in _read_csv_rows()}

    missing: list[str] = []
    for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("|")
        if len(parts) < 5:
            continue
        legacy_entry_id = parts[4].strip()
        if not legacy_entry_id:
            continue
        if legacy_entry_id not in csv_ids:
            missing.append(f"{parts[0]}|{parts[1]} → {legacy_entry_id}")

    assert not missing, "allowlist legacy_entry_id 反向引用孤儿(在 CSV 中不存在):\n  " + "\n  ".join(missing)
