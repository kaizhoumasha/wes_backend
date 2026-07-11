"""Revision B 退役旧 Inbox 表的迁移合同。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py"


def test_revision_b_migrates_all_inbox_foreign_keys_before_drop() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert source.index("UPDATE wes_biz.workline_diagnostics SET inbox_id = NULL") < source.index(
        'op.drop_table("workline_inbox"'
    )
    assert source.index("UPDATE wes_biz.runtime_holds SET source_inbox_id = NULL") < source.index(
        'op.drop_table("workline_inbox"'
    )
    assert source.index("UPDATE wes_biz.smt_inbound_handoff_source_items ") < (
        source.index('op.drop_table("workline_inbox"')
    )
    assert "SET source_pick_inbox_id = NULL WHERE source_pick_inbox_id IS NOT NULL" in source
    assert "referent_schema=RUNTIME_SCHEMA" in source
    assert source.count("fk_") >= 7


def test_revision_b_adds_canonical_workline_session_fk_and_supports_downgrade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'sa.Column("workline_session_id", sa.BigInteger(), nullable=True)' in source
    assert "fk_runtime_inbox_workline_session_id_workline_sessions" in source
    assert "ix_wes_runtime_runtime_inbox_workline_session_id" in source
    assert 'op.create_table(\n        "workline_inbox"' in source
    assert 'op.drop_column("runtime_inbox", "workline_session_id"' in source
