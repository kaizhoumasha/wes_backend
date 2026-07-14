"""Revision B 退役旧 Inbox 表的迁移合同。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py"
INDEX_MIGRATION = REPO_ROOT / "migrations/versions/20260714_1103_e0d58415afc9_create_runtime_inbox_indexes_.py"


def test_revision_b_migrates_all_inbox_foreign_keys_before_drop() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    insert_position = source.index("INSERT INTO wes_runtime.runtime_inbox")
    update_loop_position = source.index(
        "for table_name, column_name, _old_constraint, new_constraint, update_statement in _DEPENDENT_FOREIGN_KEYS:"
    )
    drop_position = source.index('op.drop_table("workline_inbox"')
    for update_target in (
        "SET inbox_id = runtime.id",
        "SET source_inbox_id = runtime.id",
        "SET source_pick_inbox_id = runtime.id",
    ):
        assert update_target in source
    assert insert_position < update_loop_position < drop_position
    assert "legacy-workline-inbox:" in source
    assert "PRE_CUTOVER_AUDIT_ONLY" in source
    assert "SET inbox_id = NULL" not in source
    assert "SET source_inbox_id = NULL" not in source
    assert "SET source_pick_inbox_id = NULL" not in source
    assert "referent_schema=RUNTIME_SCHEMA" in source
    assert source.count("fk_") >= 7


def test_revision_b_adds_canonical_workline_session_fk_and_supports_downgrade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    index_source = INDEX_MIGRATION.read_text(encoding="utf-8")
    assert 'sa.Column("workline_session_id", sa.BigInteger(), nullable=True)' in source
    assert "fk_runtime_inbox_workline_session_id_workline_sessions" in source
    assert "ix_wes_runtime_runtime_inbox_workline_session_id" not in source
    assert "ix_wes_runtime_runtime_inbox_workline_session_id" in index_source
    assert "postgresql_concurrently=True" in index_source
    assert 'op.create_table(\n        "workline_inbox"' in source
    assert "Revision B downgrade refused" in source
    assert 'op.drop_column("runtime_inbox", "workline_session_id"' in source
