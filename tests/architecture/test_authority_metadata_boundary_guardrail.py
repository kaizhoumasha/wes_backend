"""AUTHORITY_METADATA_BOUNDARY guardrail: 查询响应强制 scope/authority/source/evidence_at。

复用 tests/support/workline_contracts.py 的 authority validator。
"""

from __future__ import annotations

from tests.support.workline_contracts import (
    AuthorityMetadata,
    validate_authority_metadata,
)


def test_authority_metadata_boundary_complete_authority_metadata_accepted():
    meta = AuthorityMetadata(scope="s", authority="WMS", source="wms", evidence_at="t")
    ok, _ = validate_authority_metadata(meta)
    assert ok


def test_authority_metadata_boundary_missing_any_field_rejected():
    for missing in ("scope", "authority", "source", "evidence_at"):
        kwargs = {"scope": "s", "authority": "WMS", "source": "wms", "evidence_at": "t"}
        kwargs[missing] = ""
        ok, reason = validate_authority_metadata(AuthorityMetadata(**kwargs))  # type: ignore[arg-type]
        assert not ok
        assert missing.upper() in (reason or "")
