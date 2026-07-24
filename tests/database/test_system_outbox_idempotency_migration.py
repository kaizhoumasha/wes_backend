"""SystemOutbox 幂等键迁移合同。"""

from pathlib import Path


def test_system_outbox_idempotency_key_migration_is_nullable_and_reversible() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    migrations = sorted((repo_root / "migrations/versions").glob("*add_system_outbox_idempotency_key.py"))

    assert len(migrations) == 1
    source = migrations[0].read_text(encoding="utf-8")

    assert 'revision: str = "6ea20f0c0d22"' in source
    assert 'down_revision: Union[str, Sequence[str], None] = "bba1942e9ea8"' in source
    assert 'sa.Column("idempotency_key", sa.String(length=160), nullable=True)' in source
    assert 'op.drop_column("system_outbox", "idempotency_key", schema="wes_biz")' in source
    assert "op.execute" not in source
