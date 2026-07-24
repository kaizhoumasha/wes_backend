"""未发布 EFFECT 状态合同以清空旧账本完成零兼容切换。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20260722_1053_8fb4b595a85c_converge_effect_state_contract.py"


def test_effect_state_cutover_clears_incompatible_ledgers_before_schema_changes() -> None:
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    upgrade = source[source.index("def upgrade()") : source.index("def downgrade()")]

    truncate_at = upgrade.index("TRUNCATE TABLE")
    first_schema_change_at = min(upgrade.index("op.drop_index("), upgrade.index("op.add_column("))

    assert truncate_at < first_schema_change_at
    assert "wes_runtime.runtime_intent_logs" in upgrade
    assert "wes_biz.system_outbox" in upgrade
    assert "wes_biz.workline_dispatch_attempts" in upgrade
    assert "RESTART IDENTITY CASCADE" in upgrade


def test_effect_state_cutover_does_not_backfill_legacy_identity_or_status() -> None:
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    upgrade = source[source.index("def upgrade()") : source.index("def downgrade()")]

    assert "UPDATE " not in upgrade
    assert "INSERT " not in upgrade


def test_effect_state_downgrade_clears_target_state_before_restoring_legacy_constraints() -> None:
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]

    assert downgrade.index("TRUNCATE TABLE") < downgrade.index("op.create_check_constraint(")
    assert "wes_runtime.runtime_intent_logs" in downgrade
    assert "wes_biz.system_outbox" in downgrade
    assert "wes_biz.workline_dispatch_attempts" in downgrade
    assert "RESTART IDENTITY CASCADE" in downgrade
