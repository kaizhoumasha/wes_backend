"""QA 北向 operation 空账本快照回归测试。"""

from __future__ import annotations

from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    NorthboundOperationsRepository,
)
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog

# Regression: ISSUE-004 — 平台空账本快照必须显示 SLO catalog 中的同步 QUERY
# Found by /qa on 2026-07-24

_QUERY_OPERATION = "wms.inventory.query_inventory@v1"


class _Rows:
    def all(self) -> tuple[()]:
        return ()


class _EmptyDatabase:
    async def execute(self, _statement) -> _Rows:
        return _Rows()


async def test_platform_snapshot_includes_query_operation_without_runtime_rows() -> None:
    catalog = build_provider_catalog()
    rows = await NorthboundOperationsRepository(provider_catalog=catalog).load_snapshot(
        _EmptyDatabase(),
        tenant_id=None,
        workline_id=None,
    )

    query_rows = tuple(row for row in rows if row.operation_identity == _QUERY_OPERATION)
    assert len(query_rows) == 1
    assert query_rows[0].provider_profile_identity == catalog.profile_identity
    assert query_rows[0].mode == "QUERY"
