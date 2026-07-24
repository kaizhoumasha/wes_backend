"""QA 北向 operation 空账本快照回归测试。"""

from __future__ import annotations

from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    NorthboundOperationsRepository,
)

# Regression: ISSUE-004 — 平台空账本快照必须显示 SLO catalog 中的同步 QUERY
# Found by /qa on 2026-07-24
# Report: .gstack/qa-reports/qa-report-127-0-0-1-8011-2026-07-24.md

_QUERY_OPERATION = "wms.inventory.query_inventory@v1"


class _Rows:
    def all(self) -> tuple[()]:
        return ()


class _EmptyDatabase:
    async def execute(self, _statement) -> _Rows:
        return _Rows()


async def test_platform_snapshot_includes_query_operation_without_runtime_rows() -> None:
    rows = await NorthboundOperationsRepository().load_snapshot(
        _EmptyDatabase(),
        tenant_id=None,
        workline_id=None,
    )

    query_rows = tuple(row for row in rows if row.operation_identity == _QUERY_OPERATION)
    assert len(query_rows) == 1
    assert query_rows[0].provider_profile_identity.endswith(".sandbox")
    assert query_rows[0].mode == "QUERY"
