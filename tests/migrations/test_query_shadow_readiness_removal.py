"""QUERY shadow/readiness 删除迁移合同。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_query_shadow_readiness_removal_migration_drops_and_can_rebuild_empty_schema() -> None:
    migrations = tuple((REPO_ROOT / "migrations/versions").glob("*remove_query_shadow_readiness.py"))
    assert len(migrations) == 1

    source = migrations[0].read_text(encoding="utf-8")
    upgrade_source, downgrade_source = source.split("def downgrade()", maxsplit=1)
    for database_object in (
        "query_shadow_comparisons",
        "query_shadow_readiness_reports",
        "query_shadow_readiness_approvals",
        "query_shadow_raise_exception",
    ):
        assert database_object in upgrade_source
        assert database_object in downgrade_source

    assert "DROP TABLE wes_runtime.query_shadow_comparisons CASCADE" in upgrade_source
    assert "CREATE TABLE wes_runtime.query_shadow_comparisons" in downgrade_source
    assert "PARTITION BY RANGE (observed_at)" in downgrade_source
