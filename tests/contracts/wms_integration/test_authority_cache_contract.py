"""BC-09 WMS Authority Cache 行为契约。

验收: WMS query 可短缓存, 但 cache hit response 仍含 authority=WMS / source /
evidence_at, 不得改变 WMS 权威。必须通过; 不可 skip。
"""

from __future__ import annotations

from tests.support.workline_contracts import (
    AuthorityMetadata,
    WmsQueryCacheEntry,
    validate_authority_metadata,
    wms_cache_preserves_authority,
)


def test_wms_cache_hit_preserves_authority():
    entry = WmsQueryCacheEntry(
        payload={"material_code": "M001", "qty": 10},
        authority=AuthorityMetadata(
            scope="WORKLINE_LOCAL",
            authority="WMS",
            source="wms_inventory_query",
            evidence_at="2026-06-25T10:00:00Z",
        ),
        cached_at=1000.0,
    )
    ok, reason = wms_cache_preserves_authority(entry)
    assert ok
    assert reason is None


def test_wms_cache_rejects_non_wms_authority():
    entry = WmsQueryCacheEntry(
        payload={},
        authority=AuthorityMetadata(
            scope="WORKLINE_LOCAL",
            authority="WES",  # 影子 WMS 风险
            source="local_projection",
            evidence_at="2026-06-25T10:00:00Z",
        ),
        cached_at=1000.0,
    )
    ok, reason = wms_cache_preserves_authority(entry)
    assert not ok
    assert reason == "AUTHORITY_NOT_WMS"


def test_authority_metadata_missing_scope_rejected():
    ok, reason = validate_authority_metadata(AuthorityMetadata(scope="", authority="WMS", source="s", evidence_at="t"))
    assert not ok
    assert reason == "MISSING_SCOPE"


def test_authority_metadata_missing_authority_rejected():
    ok, reason = validate_authority_metadata({"scope": "s", "authority": "", "source": "s", "evidence_at": "t"})
    assert not ok
    assert reason == "MISSING_AUTHORITY"


def test_authority_metadata_none_rejected():
    ok, reason = validate_authority_metadata(None)
    assert not ok
    assert reason == "MISSING_AUTHORITY_METADATA"
