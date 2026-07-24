"""EFFECT 双账本状态数据库合同。"""

from __future__ import annotations

from pathlib import Path

MIGRATION_ROOT = Path(__file__).parents[3] / "migrations" / "versions"


def test_effect_state_contract_has_one_schema_only_convergence_migration() -> None:
    migrations = list(MIGRATION_ROOT.glob("*_converge_effect_state_contract.py"))

    assert len(migrations) == 1
    migration = migrations[0].read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "8db8cbba582c"' in migration
    assert '"dispatch_key", sa.String(length=240), nullable=False' in migration
    assert '"ux_runtime_intent_log_dispatch_key"' in migration
    assert '"ck_runtime_intent_logs_runtime_intent_status"' in migration
    assert "'RETRY_WAIT'" in migration
    assert "'UNKNOWN'" in migration
    assert "'TECHNICAL_FAILED'" in migration
    assert "UPDATE " not in migration.upper()
    assert "INSERT INTO " not in migration.upper()
