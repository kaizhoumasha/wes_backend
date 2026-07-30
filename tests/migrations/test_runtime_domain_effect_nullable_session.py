"""Runtime domain EFFECT 不依赖 plugin execution session 的 migration 合同。"""

from __future__ import annotations

from pathlib import Path


def test_runtime_intent_log_execution_session_nullable_migration_is_narrow_and_reversible() -> None:
    migrations = list(Path("migrations/versions").glob("*_allow_runtime_domain_effect_without_*.py"))

    assert len(migrations) == 1
    migration = migrations[0].read_text(encoding="utf-8")
    assert migration.count("op.alter_column(") == 2
    assert migration.count('"runtime_intent_logs"') == 2
    assert migration.count('"execution_session_id"') == 2
    assert "existing_type=sa.Integer()" in migration
    assert "nullable=True" in migration
    assert "nullable=False" in migration
    assert 'schema="wes_runtime"' in migration
    assert "drop_constraint" not in migration
    assert "create_foreign_key" not in migration
