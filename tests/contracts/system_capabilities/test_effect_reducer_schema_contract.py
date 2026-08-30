"""T8d ReconciliationCase schema-only migration 合同。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]
RECONCILIATION_CASE_MIGRATION = (
    ROOT / "migrations" / "versions" / "20260723_0027_c325aab03400_add_effect_reconciliation_cases.py"
)


def _migration_source() -> str:
    assert RECONCILIATION_CASE_MIGRATION.is_file()
    source = RECONCILIATION_CASE_MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "c325aab03400"' in source
    return source


def test_reconciliation_case_migration_is_schema_only_and_follows_t8c_head() -> None:
    source = _migration_source()

    assert 'down_revision: Union[str, Sequence[str], None] = "8de7cb4de434"' in source
    assert '"reconciliation_cases"' in source
    assert 'schema="wes_runtime"' in source
    assert '"wes_runtime.runtime_intent_logs.id"' in source
    assert '"ck_reconciliation_cases_resolution_state"' in source
    assert '"ux_reconciliation_cases_open_dispatch_key"' in source
    assert "postgresql_where=sa.text(\"status = 'OPEN'\")" in source
    assert "op.execute" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source


def test_reconciliation_case_migration_downgrade_only_drops_the_new_table() -> None:
    source = _migration_source()
    downgrade = source.split("def downgrade()", maxsplit=1)[1]

    assert 'op.drop_table("reconciliation_cases", schema="wes_runtime")' in downgrade
    assert "runtime_intent_logs" not in downgrade
