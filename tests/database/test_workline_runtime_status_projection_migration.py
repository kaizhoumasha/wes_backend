"""WorkLine runtime status projection destructive migration smoke tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"
MIGRATION_NAME = "drop_legacy_workline_runtime_residuals"
LEGACY_WORKLINE_COLUMNS = (
    "runtime_status",
    "active_safety_incident_id",
    "stopped_at",
    "stopped_reason",
    "resumed_at",
)


def _migration_text() -> str:
    matches = sorted(MIGRATIONS_DIR.glob(f"*_{MIGRATION_NAME}.py"))
    assert len(matches) == 1, f"expected exactly one migration matching {MIGRATION_NAME}"
    return matches[0].read_text(encoding="utf-8")


def test_migration_creates_runtime_projection_and_unique_workline_index() -> None:
    text = _migration_text()

    assert 'PROJECTION_TABLE = "workline_runtime_status_projections"' in text
    assert "op.create_table(" in text
    assert '"wes_runtime"' in text
    assert "ux_wrt_status_proj_workline" in text
    assert "runtime_status IN ('READY', 'STOPPED', 'STARTING', 'ESTOPPED', 'RECONCILING')" in text


def test_migration_backfills_from_legacy_workline_runtime_columns() -> None:
    text = _migration_text()

    assert "INSERT INTO" in text
    assert '"{RUNTIME_SCHEMA}"."{PROJECTION_TABLE}"' in text
    assert '"{BIZ_SCHEMA}"."{WORK_LINES_TABLE}"' in text
    for column_name in LEGACY_WORKLINE_COLUMNS:
        assert column_name in text
    assert "'migrated_from', 'wes_biz.work_lines'" in text
    assert "'migration_revision', 'f0851c5bcfdb'" in text


def test_migration_drops_legacy_runtime_columns_and_bin_transit_table() -> None:
    text = _migration_text()

    assert "DROP CONSTRAINT IF EXISTS" in text
    assert 'DROP TABLE IF EXISTS "{BIZ_SCHEMA}"."{BIN_TRANSIT_TABLE}" CASCADE' in text
    for column_name in LEGACY_WORKLINE_COLUMNS:
        assert f'_drop_workline_column_if_exists("{column_name}")' in text or f'"{column_name}"' in text


def test_migration_has_operational_timeouts_and_downgrade_data_boundary() -> None:
    text = _migration_text()

    assert "SET LOCAL lock_timeout = '5s'" in text
    assert "SET LOCAL statement_timeout = '60s'" in text
    assert "not recoverable" in text
    assert "database snapshot" in text
