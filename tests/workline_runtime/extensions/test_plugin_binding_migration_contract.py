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
