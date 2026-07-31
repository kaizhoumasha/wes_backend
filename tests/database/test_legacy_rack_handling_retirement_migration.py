"""旧 Rack / Handling 聚合表退役 migration 合同。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_GLOB = "*_drop_legacy_rack_handling_operation_*.py"


def _migration_source() -> str:
    migrations = sorted((REPO_ROOT / "migrations" / "versions").glob(MIGRATION_GLOB))
    assert len(migrations) == 1
    return migrations[0].read_text(encoding="utf-8")


def test_upgrade_drops_legacy_tables_in_foreign_key_safe_order() -> None:
    source = _migration_source()
    expected_order = (
        'op.drop_table("handling_operation_steps", schema="wes_biz")',
        'op.drop_table("handling_operation_moves", schema="wes_biz")',
        'op.drop_table("handling_operations", schema="wes_biz")',
        'op.drop_table("rack_tasks", schema="wes_biz")',
        'op.drop_table("rack_operations", schema="wes_biz")',
    )

    positions = [source.index(statement) for statement in expected_order]
    assert positions == sorted(positions)


def test_migration_has_no_legacy_schema_compatibility_downgrade() -> None:
    source = _migration_source()
    downgrade = source.split("def downgrade()", maxsplit=1)[1]

    assert 'down_revision: Union[str, Sequence[str], None] = "f557c7b749b1"' in source
    assert "raise RuntimeError" in downgrade
    assert "op.create_table" not in downgrade
