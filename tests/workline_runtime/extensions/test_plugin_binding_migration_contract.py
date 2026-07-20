"""插件 binding pin 的 Alembic 数据库约束合同。"""

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[3] / "migrations/versions/20260717_0739_fa15ba0aef65_add_workline_plugin_runtime_binding.py"
)


def test_pin_versions_have_named_database_checks_and_precise_downgrade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert source.count('op.f(f"ck_{table}_plugin_binding_version_positive")') == 2
    assert source.count('op.f(f"ck_{table}_plugin_state_version_non_negative")') == 2
    assert "plugin_binding_version IS NULL OR plugin_binding_version >= 1" in source
    assert "plugin_state_version >= 0" in source


def test_legacy_intent_duplicates_are_rejected_before_unique_constraint() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    guard_position = source.index("LEGACY_INTENT_DUPLICATE_IDENTITY")
    constraint_position = source.index('"uq_runtime_intent_log_effect_identity"')

    assert "GROUP BY provider_code, idempotency_key" in source
    assert "HAVING COUNT(*) > 1" in source
    assert "LIMIT 10" in source
    assert "truncated=%s" in source
    assert "duplicate_group_count" in source
    assert "groups_truncated=%s" in source
    assert guard_position < constraint_position
