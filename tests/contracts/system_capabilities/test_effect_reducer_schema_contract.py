"""T8d ReconciliationCase schema-only migration 与模块导出合同。"""

from __future__ import annotations

from pathlib import Path

from src.app.runtime import orchestration
from src.app.runtime.orchestration import repositories, services

ROOT = Path(__file__).parents[3]


def _migration_source() -> str:
    matches = [
        path
        for path in (ROOT / "migrations" / "versions").glob("*.py")
        if "reconciliation_cases" in path.read_text(encoding="utf-8")
    ]
    assert len(matches) == 1
    return matches[0].read_text(encoding="utf-8")


def test_effect_reducer_runtime_symbols_are_exported_from_their_owner_modules() -> None:
    assert orchestration.ReconciliationCase.__name__ == "ReconciliationCase"
    assert orchestration.ReconciliationCaseStatus.OPEN.value == "OPEN"
    assert repositories.EffectReducerRepository.__name__ == "EffectReducerRepository"
    assert services.EffectReducer.__name__ == "EffectReducer"


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
