"""SystemOutbox 共享 EXTERNAL_HTTP 认证组合迁移合同。"""

from pathlib import Path


def test_external_http_auth_modes_migration_is_generated_schema_only_and_reversible() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    migrations = sorted((repo_root / "migrations/versions").glob("*allow_external_http_none_auth.py"))

    assert len(migrations) == 1
    source = migrations[0].read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "be496b91f3e3"' in source
    assert '"network_trust_mode"' in source
    assert "sa.String(length=50)" in source
    assert source.count('"ck_system_outbox_external_http_frozen_binding"') >= 4
    assert "auth_scheme = 'NONE'" in source
    assert "network_trust_mode = 'isolated_lan'" in source
    assert "auth_scheme = 'HMAC_SHA256'" in source
    assert 'op.drop_column("system_outbox", "network_trust_mode", schema="wes_biz")' in source
    assert "UPDATE " not in source.upper()
    assert "INSERT INTO " not in source.upper()
